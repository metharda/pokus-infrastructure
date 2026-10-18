# Build Prompt: `pokus-infrastructure` — Pokus Infrastructure (pinka), Infrastructure-as-Code for the Pokus Research Lab

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
6. **Create a `CLAUDE.md`** at the repo root summarizing the invariants in sections 2, 13 and 14 of this document, including the contract v2 invariants (§12, §13), so future Claude Code sessions follow them without re-reading this prompt.
7. When this document and reality disagree (e.g. a tool's flag was renamed), follow reality, explain the deviation in the commit message, and record it in `docs/IMPLEMENTATION_NOTES.md`.
8. **Documentation follows `docs/DOCS_SPEC.md`** (the Pokus Documentation Specification, committed next to this file). Write documentation **alongside** the code in every step, not at the end: required pages, page conventions, docstrings/argument specs, generated references, and the docs quality gates in that spec are part of every step's definition of done.
9. **The contract is versioned.** The manifest schema, the profile table and the reserved-name lists (§12) are published with every release and change only with a contract version bump.

---

## 1. Project context (why this repository exists)

The lab is a shared research environment for benchmarking an **AI diagnostic agent** (a locally hosted open-source LLM) on **reproducible, injected Linux faults**. "Pokus" names the project as a whole. The full system consists of this repository and the platforms that plug into it:

| Repository | Owns | Built by this prompt? |
|---|---|---|
| `pokus-infrastructure`: Pokus Infrastructure, technical name `pinka` | Host provisioning, preflight/environment detection, lab services, target sandbox infrastructure (Incus), golden image recipes, observability stack, backup/restore, doctor, environment fingerprinting, the GPU arbiter and training jobs, contract v2 with the manifest schema and the privilege profiles, the reference platform | **Yes** |
| Platforms (any repository) | A testing platform's services, training jobs and content, plugged in through one manifest validated against contract v2 | No — only contract v2 (§12) |
| `pokus-platform`: Pokus Platform, technical name `potka`, the first platform | Orchestrator, agent runner, LLM gateway, policy bundle (OPA), executor, injector, verifier, evaluator, DB migrations, `potka` CLI, and the scenario catalog (`catalog/`: scenario definitions, ground truth, taxonomy, blueprints' scenario-facing metadata) as a restricted content package | No — only contract v2 (§12) |

`pokus-infrastructure` must be fully usable on its own: without a platform it produces a working host with all shared services, the target sandbox, observability, inference, backups and doctor.

Key requirements carried over from the lab design:

- Linux-only. The lab must not depend on Windows in any way. WSL2 is supported only as "a Linux host that happens to run on Windows".
- **Portable across mainstream Linux distributions** (Debian/Ubuntu, Fedora/RHEL-compatible, openSUSE/SLES, Arch, and their derivatives), across bare metal, VMs and WSL2, and across x86_64 hosts with or without a GPU. Section §19 defines how; it takes precedence over any distro-specific wording elsewhere in this document.
- Portable: a new machine (bare metal, VM, or WSL2) is built with one command from this repo, and existing lab state can be restored onto it.
- A small team of human researchers (the reference setup has three) with separate Linux accounts and SSH keys, a shared `pinka` group, shared `/srv/pinka` research space, and privilege profiles rendered from the platform's manifest (§12).
- Remote access only via Tailscale. No public ports, no router port forwarding, never Tailscale Funnel.
- Test targets run in **Incus** (system containers by default, VMs for kernel-level scenarios), never in the lab's own Docker daemon. The AI agent must never be able to harm the host, lab services, or personal files.
- Local inference with **vLLM** on a single NVIDIA GPU (reference: RTX 4070 12 GB). No external model APIs.
- The research loop: test, train an adapter on the same GPU, serve the new model, re-test on the same benchmark (§7.18).
- Observability: OpenTelemetry Collector, Prometheus, Loki, Tempo, Grafana.
- Experiment database: PostgreSQL, one database per platform. Artifacts: the platform's state under `/srv/pinka/platforms/<platform>/state/`.
- Academic reproducibility: everything pinned, every lab session fingerprinted, results auditable.
- No Kubernetes, Slurm, Ray, Nomad, or GPU schedulers. Single machine, clarity over enterprise complexity. The GPU arbiter (§7.18) is a mode switch on one machine, not a scheduler.

**Reference test profile** (used to choose defaults such as the `ada-12g` vLLM profile and for hardware testing; it describes a class of machine, not any particular installation): x86_64, 24 logical CPUs, 32 GB RAM, one 12 GB NVIDIA Ada-generation GPU (e.g. RTX 4070 class), Ubuntu 24.04 under WSL2 on a workstation that is regularly powered off. Deployments may use any hardware and environment the support matrix allows; the design must tolerate hosts that are not always on, and labs must be movable between machines (e.g. from WSL2 to bare metal).

---

## 2. Supported environments and the preflight gate (core feature)

Before anything is installed, a **preflight tool** must detect what kind of machine it is running on, decide whether the lab is supported there, decide which optional features are available, and either continue, continue in degraded mode with explicit consent, ask the operator to take an action, or abort.

### 2.1 Implementation constraints

- Location: `preflight/pinka_preflight/` (Python package) with entry point `preflight/pinka-preflight` (thin wrapper script).
- **Python standard library only**, and source-compatible with **Python ≥ 3.9** (no `match`, no 3.10+ only syntax; use `from __future__ import annotations`) so `pinka preflight` can also run on an old system interpreter. During bootstrap it runs on the uv-managed CPython (§19.3). No third-party imports.
- Every filesystem and command access goes through an abstraction that accepts a `--root <path>` (fake filesystem root) and a command-runner that can be replaced by fixtures. This makes detection fully unit-testable without the real hardware (see §11).
- Output:
  - human-readable summary to stderr (colored only when stderr is a TTY),
  - machine-readable JSON report to stdout or `--output <file>`,
  - on a real run (not `--root`), the report is written to `/etc/pinka/host-profile.json` (0644 root:root) and archived as `/var/lib/pinka/preflight/<UTC-timestamp>.json`.
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
| Memory | `MemTotal`, `SwapTotal` from `/proc/meminfo`. The firmware and the kernel keep part of the installed memory, so every memory threshold (§2.3) compares `MemTotal` with 0.94 × its nominal value: a nominal 16 GiB machine, which reports about 15.1–15.6 GiB, passes 16 GiB |
| Disk | free space and filesystem type for `/var/lib` (Incus/Docker), the configured `/srv/pinka` parent and every other mount point that holds an item of the requirements computation (§2.3: the Incus pool's loop file, `docker_data_root`, `/opt/pinka`); block devices from `/sys/block` (for informational listing only) |
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
- Driver and CUDA: for every usable NVIDIA GPU, preflight records the highest CUDA version the loaded driver supports (from `nvidia-smi`; on WSL2 that is the Windows driver; verify the query at implementation) and compares it with the CUDA version the pinned vLLM image is built on (`versions.lock.yml`, §10.1). A driver that is too old makes `gpu_inference` unavailable with a clear remediation: "the NVIDIA driver supports CUDA <x>, the pinned vLLM image needs <y>: install a driver branch that supports CUDA <y>" (on WSL2: "update the NVIDIA driver on Windows"; never a Linux driver inside WSL). Doctor repeats the check on every run (§9).

**Conflicting software** (each reported as `conflict` with severity `blocking` or `warning` and a remediation string):

| Conflict | Detection | Severity |
|---|---|---|
| Docker Desktop WSL integration | `/mnt/wsl/docker-desktop*` exists, or `docker` on PATH resolves into `/mnt/wsl/` or `docker context ls` shows `desktop-linux`, or `/var/run/docker.sock` is a symlink into `/mnt/wsl` | blocking |
| Snap Docker | `snap list docker` succeeds | blocking |
| LXD installed | `snap list lxd` or `lxd` binary/`lxdbr0` | info: a pre-existing component (below), left as it is; needs_action when Incus next to it would clash (§7.7). Migrating LXD to Incus is out of scope |
| Host firewall manager active (ufw, firewalld) | `systemctl is-active ufw/firewalld` | info — the firewall role integrates with it instead of replacing it (§19.6); report which backend will be used |
| Image-based / immutable OS | `/run/ostree-booted` (Fedora Atomic, CoreOS), `/etc/NIXOS`, transactional-update with read-only root (openSUSE MicroOS/Aeon), Ubuntu Core (`/writable` + snapd-only) | blocking — remediation: run a supported distro in a VM on this host (`vm` provider) |
| Non-systemd init or musl libc | PID 1 not systemd (Alpine/OpenRC, Devuan, Void, Gentoo OpenRC), musl via `ldd --version` / `/lib/ld-musl-*` | blocking — same VM remediation |
| SELinux / AppArmor state | `getenforce` or `/sys/fs/selinux/enforce`; `/sys/kernel/security/apparmor` | info — recorded; the `mac` role adapts (§19.7). SELinux in enforcing mode is supported and must never be disabled by the repo |
| Package manager lock / unfinished transaction | apt/dpkg locks, `dnf` history incomplete, zypper lock, pacman `db.lck` | needs_action |
| Linux NVIDIA driver inside WSL | see above | blocking |
| Podman installed | `podman` binary, `podman.socket` or `podman.service` | info: a pre-existing component (below), left as it is; needs_action when Podman provides Docker's command or socket (`podman-docker`, or `/var/run/docker.sock` served by Podman), which Docker Engine needs |
| Docker Engine installed before pinka | Docker Engine present and the install record (§7.21) does not name pinka as its origin | info: a pre-existing component, used as it is (§7.6); needs_action when a GPU feature is on and CDI is off in it (`docker info` reports no CDI spec directories), or when it lacks the Compose v2 plugin or buildx; the remediation names the exact change for the owner to make |
| Tailscale logged in before pinka | `tailscale status --json` reports the node running and the install record does not name pinka as the one that joined it | info: an adopted node, used as it is (§7.4); needs_action when Funnel is on (`tailscale funnel status`) or Tailscale SSH is on (the node's `RunSSH` preference; verify the query) |
| Existing Incus with non-managed config | `incus` installed and `/var/lib/incus` not empty and no `/etc/pinka/managed-incus` marker | blocking unless `--adopt-incus` |
| Lab port in use | a configured lab port (§12.11: the defaults plus the `ports:` overrides) is bound by a process outside the lab (`/proc/net/tcp*`, `ss -ltnp`) | needs_action — remediation names the process and the `ports:` override |
| VM collector address conflict (with `vm_targets`) | `vm_collector_address` is already assigned or routed on the host (`ip -j addr`, `ip -j route`), or overlaps the Docker address pools, an Incus network or the tailnet range `100.64.0.0/10` | needs_action — remediation: choose another address in host_vars |

**Pre-existing components.** Docker Engine, Tailscale, LXD, Podman, Incus, an NVIDIA driver and user accounts may exist before pinka is installed. Preflight lists each in the host profile (`components`: installed, version, running, and its origin from the install record of §7.21: `pinka` or `pre-existing`); the rules for each are in §7.4 (Tailscale), §7.6 (Docker), §7.7 (Incus and LXD), §6.1 (accounts) and §7.21. Pinka never changes, stops, restarts or reconfigures a component it did not install (a Tailscale that is installed but not logged in is not in use and is joined, §7.4). Where the lab cannot run without such a change, preflight reports `needs_action` with the exact change for the owner to make, so nothing on the host changes and no override continues past it.

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
  # pending_verification: shown as "pending verification" until make test-distros records a passing result (§19.9)
  - {match: {id: ubuntu, version_id: "26.04"}, family: debian, tier: supported, release_model: fixed, pending_verification: true,
     note: "Incus comes from Ubuntu's universe component, which has no guaranteed security updates (§7.7)"}
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
  # RHEL-compatible 8 (Oracle Linux 8 included) predates the unified cgroup v2 default; matched before the ID_LIKE rows
  - match: {id_or_like_in: [rhel, centos, almalinux, rocky, ol], version_major_in: ["8"]}
    tier: unsupported
    reason: "RHEL-compatible release 8 is not supported. Its cgroup v2 hierarchy can be enabled with the kernel argument systemd.unified_cgroup_hierarchy=1, but install a supported release (9 or 10) instead"
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

minimums:                     # nominal values; MemTotal is compared with 0.94 × the nominal value (§2.2)
  memory_gib:  {floor: 8}       # the core stack; below it → unsupported. Required and recommended memory and
                                # the free disk per mount point are computed from the choices (below the YAML)
  cpu_threads: {required: 8,  recommended: 16}

core_requirements:            # failing any of these → unsupported (or needs_action)
  - systemd_pid1
  - cgroup2_unified
  - cgroup2_controllers: [cpu, cpuset, memory, io, pids]
  - dev_net_tun
  - kernel_features: [overlay, br_netfilter, nf_tables, veth]
  - no_blocking_conflicts

features:                     # optional; each becomes a boolean feature flag
  gpu_inference:
    requires: [nvidia_gpu_usable, gpu_vram_gib_min: 8, gpu_compute_capability_min: "7.0", driver_cuda_min: "@versions.lock:containers.vllm.cuda"]
    on_missing: degrade       # lab installs without vLLM; a too-old driver prints the remediation of §2.2
  gpu_training:
    requires: [nvidia_gpu_usable, gpu_vram_gib_min: 8, gpu_compute_capability_min: "7.0"]
    on_missing: degrade       # no GPU training jobs; their CPU-only variant still runs (§7.18)
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
      # with install_nvidia_driver: auto and the operator's consent to MOK enrollment (§3.2) the rule becomes a
      # planned action: a warning recorded in the host profile, performed by bootstrap. manual stays needs_action;
      # skip leaves the GPU unused (degraded)
      - id: secure_boot_mok
        when: {secure_boot: enabled, nvidia_driver_installed: false, nvidia_gpu_present: true, nvidia_driver_mode_in: [auto, manual]}
        action: "Secure Boot is on: the NVIDIA DKMS module must be signed and a MOK enrolled at next boot. With install_nvidia_driver: auto, consent with --accept-mok-enrollment (or in the installer); otherwise install the driver yourself. See docs/providers/baremetal.md"
        planned_when: {nvidia_driver_mode: auto, mok_enrollment_accepted: true}
        planned: "Secure Boot is on: bootstrap installs the NVIDIA driver, imports a Machine Owner Key with your one-time password and stops with 76; reboot, enroll the key at the blue MOK Manager screen, then re-run bootstrap"
  vm:
    warnings:
      - when: {hypervisor_in: [microsoft, vmware, oracle], kvm_usable: false}
        message: "Nested virtualization unavailable in this hypervisor; VM-class targets disabled"
```

Note: storage must end up as either `btrfs_pool` or `lvm_thin_pool`. If neither is available, the result is `unsupported`, because a `dir` storage backend cannot enforce disk quotas and disk-exhaustion scenarios would fill the host disk.

**Requirements computed from the choices.** Memory above the floor and free disk are not fixed numbers: they follow what the operator chooses.

- The data lives in `preflight/requirements.yml` (compiled to committed, drift-checked JSON like the matrix): per component (base system, PostgreSQL, observability, OPA, Incus and each running target, vLLM with the selected model, a training job, the installer, bootstrap itself) its memory, CPU and disk need, the source of each number, and whether it is `measured` or `estimated`. Every number is an estimate until real-machine test 4.15 of the test specification measures it.
- Preflight takes the choices as `--plan <choices.json>` (features, `node_role`, the selected model, the Incus pool source and size, the number of concurrent targets, the golden images to build, the retention settings), as bootstrap and the installer write them; without `--plan` it uses the configured defaults. It prints the breakdown, and the host profile records it (`requirements`). Bootstrap, the installer and doctor use this one computation, so they show the same numbers.
- **Memory:** below the floor (8 GiB nominal: the core stack with no target running) the outcome is `unsupported`. Above it, the required and recommended values are computed from `gpu_inference`, `gpu_training`, `node_role`, the number of concurrent targets and the selected model's size: below required → `unsupported`, with the breakdown and the choices that would fit; between required and recommended → `degraded` (consent as for any degraded outcome).
- **Disk, checked at install:** per mount point, the sum of the selected models' files (sizes in `models.lock.yml`, §7.12), the container images of the enabled features (each image's compressed size from `versions.lock.yml` × 2.5 as an estimate; once an image is pulled on a host, preflight and doctor use its real size there), the Incus pool (a loop file counts fully on its mount point, a dedicated device on none), the golden images to build (each blueprint's estimated size in its `image.yaml`, §7.13) and 5 GiB for pinka itself. A shortfall is refused and listed item by item (for example `model qwen3-8b-awq: 5.7 GiB, missing 2.1 GiB on /srv`).
- **Disk, growth:** the pool size and every retention setting are configurable (Prometheus by time and size, Loki, Tempo, backups, artifacts); the installer shows the expected growth per day and week, and doctor warns before a disk fills (§9).
- The Incus `bench` project's `limits.memory` follows the chosen number of concurrent targets (§7.7).

### 2.4 Outcomes and exit codes

| Exit code | Outcome | Meaning | Bootstrap behavior |
|---|---|---|---|
| 0 | `supported` | All core requirements met; features computed | continue |
| 10 | `degraded` | Core OK, and one or more optional features unavailable or memory between the computed required and recommended values (§2.3) | continue only with `--accept-degraded` or interactive confirmation listing exactly which features are disabled and what that means for research (e.g. "no GPU: vLLM will not be installed; inference campaigns cannot run on this host") |
| 20 | `unsupported` | Core requirement failed, unsupported OS/arch/environment, blocking conflict, or memory below the floor or the computed requirement (§2.3) | abort; `--force-unsupported` continues but marks the host profile `override.force_unsupported=true`, which is carried into every environment fingerprint |
| 30 | `needs_action` | Fixable by operator action (reboot, `wsl --terminate`, enroll MOK, remove conflict, change a pre-existing component as named, §2.2) | print exact remediation steps and abort; re-run after action |
| 40 | `preflight_error` | Detection itself failed unexpectedly | abort with traceback in log |

Also report `tier` separately (`supported` / `tested` / `experimental`) and `release_model` (`fixed` / `fast` / `rolling`). `supported` and `tested` continue without extra consent (`tested` prints a note that GPU paths are not hardware-tested on this distro). `experimental` requires `--accept-experimental` (or interactive confirmation). `rolling` prints a reproducibility warning (§19.5). All of it is recorded in the profile.

A `needs_action` rule may name a **planned action** that bootstrap performs itself once the operator consents (today only `secure_boot_mok`, §3.2). With the consent, preflight records it as a warning under `planned_actions` in the host profile instead of `needs_action`, and the outcome is computed without it. Preflight takes the inputs these rules need as options (`--nvidia-driver auto|manual|skip`, `--accept-mok-enrollment`), which bootstrap and the installer pass.

Bootstrap's own exit codes are listed in §5.2 (step 8) and, for uninstall, in §5.4.

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
  "wsl": {"systemd": true, "automount": false, "interop": false, "append_windows_path": false, "hostname": "pinka-lab"},
  "hardware": {"cpu_model": "...", "cpu_threads": 20, "memory_gib": 22.0, "swap_gib": 8.0},
  "gpu": {"present": true, "usable": true, "name": "NVIDIA GeForce RTX 4070", "vram_gib": 12.0,
          "driver": "...", "compute_capability": "8.9", "path": "wsl-dxg"},
  "capabilities": {"cgroup2": true, "kvm_usable": false, "btrfs": true, "dm_thin_pool": false, "tun": true},
  "features": {"gpu_inference": true, "gpu_training": true, "vm_targets": false, "btrfs_pool": true, "lvm_thin_pool": false, "dcgm_metrics": false},
  "storage_backend": "btrfs",
  "components": {"docker": {"installed": true, "version": "...", "running": true, "origin": "pre-existing", "cdi": true},
                 "tailscale": {"installed": true, "logged_in": true, "origin": "pre-existing", "funnel": false, "ssh": false},
                 "lxd": {"installed": false}, "podman": {"installed": false}, "incus": {"installed": false}},
  "requirements": {"plan": "defaults", "memory_gib": {"floor": 8, "required": 16, "recommended": 24, "tolerance": 0.94},
                   "disk": [{"mount": "/", "required_gib": 120.5, "free_gib": 640.2, "items": ["..."]}]},
  "conflicts": [],
  "warnings": ["vm_targets disabled: /dev/kvm not available (enable nestedVirtualization on the Windows side)"],
  "needs_action": [],
  "planned_actions": [],
  "overrides": {"accept_degraded": true, "accept_experimental": false, "force_unsupported": false, "accept_mok_enrollment": false}
}
```

### 2.6 Re-validation

- Ansible's first role (`preflight_gate`) must re-run preflight in JSON mode and **fail** if the outcome, provider, or feature flags differ from `/etc/pinka/host-profile.json` without `--refresh-profile`. This prevents provisioning a host whose capabilities silently changed (e.g. GPU disappeared, KVM lost).
- `pinka doctor` re-runs preflight too (§9).

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
  - render a recommended `.wslconfig` into `/srv/pinka/services/contrib/wslconfig.recommended` for the operator to copy manually.
- Power/keep-alive: the WSL VM lifetime is controlled from Windows. Ship **optional** Windows helpers only in `contrib/windows/` (a PowerShell profile function that wraps `wsl` to start a hidden keep-alive process `wsl.exe -d <distro> --exec /usr/bin/sleep infinity` and calls `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` while the lab runs; plus the `.wslconfig` template). These are documented in `docs/providers/wsl2.md`, are never executed by bootstrap, and nothing in the Linux code depends on them.
- GPU metrics exporter: `nvidia-smi`-based exporter (DCGM is not supported on WSL; verify current status and document).

### 3.2 `provider_baremetal`

- NVIDIA driver installation (only if `nvidia_gpu_present`): `install_nvidia_driver: auto | manual | skip`. `auto` uses the per-family method in §19.8; `manual` means the operator installs the driver and preflight must then detect it as loaded. Blacklist nouveau via a managed modprobe file and rebuild the initramfs with the family's tool (`update-initramfs`, `dracut`, `mkinitcpio`).
- Secure Boot (never disabled): with `install_nvidia_driver: auto` and the operator's consent to MOK enrollment (`--accept-mok-enrollment`, the installer's consent, or `gpu.accept_mok_enrollment` in the answers file, §18.6), bootstrap installs the driver through the DKMS + MOK flow, imports a Machine Owner Key with a one-time password the operator enters (`--mok-password-stdin`: passed only to `mokutil --import` on stdin, never stored or logged) and stops with exit code 76 ("reboot, enroll the MOK at the blue MOK Manager screen, re-run bootstrap"); the re-run resumes. Without consent, or with `manual`, preflight reports `needs_action` (exit 30) with the remediation; with `skip` the GPU stays unused (degraded). A pending MOK enrollment is detected and also stops with 76.
- Reboot handling: if a driver or kernel change requires reboot, stop with exit code 76 and print instructions. Never reboot automatically unless `--allow-reboot` is passed.
- Power policy (`power_policy` in host_vars):
  - `never_sleep`: mask `sleep.target suspend.target hibernate.target hybrid-sleep.target`.
  - `lab_session_inhibit` (default for personal machines): `pinka-lab.target` pulls in `pinka-sleep-inhibit.service`, which holds a `systemd-inhibit --what=sleep:idle --mode=block` lock for as long as the lab is up.
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
pokus-infrastructure/
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
├── contract/                         # contract v2, published with every release (§12)
│   ├── platform-manifest.schema.json # the manifest's JSON Schema, with the reserved-name lists
│   ├── profiles.yml                  # the eight privilege profiles and their grants
│   ├── reserved-names.yml            # reserved names per namespace, generated from infra's own data
│   └── records/                      # schemas of DEPLOYED_FROM.json, PROVENANCE.json, LINEAGE.json, adapter MANIFEST.json
├── bootstrap/
│   ├── bootstrap.sh                  # single entry point on any supported Linux host
│   └── lib/                          # small bash helpers (logging, args, exit codes)
├── preflight/
│   ├── pinka-preflight               # wrapper script
│   ├── pinka_preflight/              # stdlib-only Python package
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
│   ├── requirements.yml              # what each component needs, for the requirements (§2.3)
│   ├── schema/host-profile.schema.json
│   └── tests/
│       ├── fixtures/                 # fake roots: see §11
│       └── test_*.py
├── installer/                        # Textual TUI front-end for bootstrap (see §18)
│   ├── pyproject.toml, uv.lock       # pinned, hash-locked
│   ├── schema/answers.schema.json
│   ├── pinka_installer/
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
│   ├── uninstall.yml                 # removal of what pinka installed (§5.4)
│   ├── inventory/
│   │   ├── hosts.yml                 # localhost, connection: local
│   │   └── host_vars/
│   │       └── example-machine.yml   # fully commented example
│   ├── group_vars/all/
│   │   ├── main.yml                  # users, layout, service config, ports
│   │   ├── versions.yml              # generated from versions.lock.yml — do not edit
│   │   └── vault.yml.example         # documented shape; real vault.yml is encrypted
│   ├── callback_plugins/pinka_events.py   # JSON event stream for the TUI (§18.5)
│   ├── filter_plugins/ (if needed)
│   └── roles/
│       ├── platform_facts/           # family/distro facts; the only place that inspects the distro (§19.1)
│       ├── pkg/                      # package-manager abstraction if module respawn is unreliable (§19.3)
│       ├── preflight_gate/
│       ├── install_record/           # what existed before pinka, what it added (§7.21)
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
│       ├── platform/                 # generic platform deployment from the manifest (§7.14)
│       ├── gpu_arbiter/              # the GPU arbiter (§7.18)
│       ├── training/                 # training jobs, datasets, adapters (§7.18)
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
│       └── dashboards/*.json         # host, GPU/vLLM, Incus targets, lab services (platform dashboards come from the manifest)
├── incus/
│   ├── preseed.yaml.j2
│   ├── profiles/*.yaml.j2
│   └── projects/*.yaml.j2
├── images/                           # golden image recipes (see §7.7)
│   ├── _base/
│   └── <blueprint>/{image.yaml,cloud-init.yaml,provision.yml,files/,tests/}
├── vllm/profiles/*.yml               # serving profiles per GPU class
├── tools/
│   ├── pinka-lab/                    # operator CLI `pinka` (Python, stdlib-only or uv-managed; see §9)
│   └── platform/                     # manifest validator and renderer (§7.14, §12)
├── users/                            # <username>.pub (one or more keys per file)
├── contrib/windows/                  # optional WSL helpers, never executed by bootstrap
├── tests/reference-platform/         # refplat, the reference platform (§7.18, §11)
└── docs/
    ├── BUILD_PROMPT.md               # this file
    ├── IMPLEMENTATION_NOTES.md
    ├── architecture.md
    ├── support-matrix.md             # generated
    ├── operations.md
    ├── security.md
    ├── reproducibility.md
    ├── contracts.md                  # contract v2 (§12)
    ├── providers/{wsl2,baremetal,vm}.md
    ├── distros/{debian,redhat,suse,archlinux,adding-a-family}.md
    ├── node-roles.md
    └── adr/0001-*.md ...
```

---

## 5. Bootstrap entry point (`bootstrap/bootstrap.sh`)

POSIX-compatible bash, `set -Eeuo pipefail`, ShellCheck-clean, `trap` for errors with the failing line, all output tee'd to `/var/log/pinka-bootstrap/<UTC-timestamp>.log` (0600 root).

### 5.1 Usage

```
sudo ./bootstrap/bootstrap.sh --machine <name> [options]

Options:
  --machine NAME           Required (except with --uninstall). Selects ansible/inventory/host_vars/NAME.yml
  --ref REF                Git ref of pokus-infrastructure being applied (recorded; default: current HEAD)
  --restore                After provisioning, run restore.yml (prompts for snapshot)
  --tags T1,T2             Pass through to ansible-playbook
  --skip-tags T1,T2        e.g. images,models for a fast first run
  --check                  Ansible check mode (dry run); preflight still runs
  --accept-degraded        Continue when optional features are missing
  --accept-experimental    Continue on an experimental-tier OS
  --force-unsupported      Continue on an unsupported host (recorded in every fingerprint)
  --refresh-profile        Accept a changed host profile
  --allow-reboot           Allow automatic reboot on bare metal / VM
  --accept-mok-enrollment  Consent to Secure Boot MOK enrollment for the automatic NVIDIA driver install (§3.2)
  --mok-password-stdin     Read the one-time MOK password from stdin; it is never stored (§3.2)
  --adopt-incus            Adopt an existing Incus installation (see §2.2)
  --uninstall              Remove what pinka installed and keep the lab's data (§5.4)
  --dry-run                With --uninstall: print the removal plan and change nothing
  --purge-data             With --uninstall: also remove the lab's data (typed machine name required)
  --confirm-machine NAME   With --uninstall --non-interactive: the typed machine name
  --skip-backup            With --uninstall: do not offer a backup first
  --remove-component C     With --uninstall on a host without an install record: also remove
                           docker|incus|tailscale|nvidia (repeatable)
  --json                   With --uninstall: print the plan and the report as JSON
  --inspect-platform       Fetch, stage and validate the platform release, print its summary, change nothing else (§7.14)
  --replace-platform NAME  Allow a platform with another name to replace the active platform NAME (§7.14)
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
2. Run `bootstrap/lib/prepreflight.sh` (POSIX sh, no bashisms): root, systemd as PID 1, glibc, supported architecture, `curl` or `wget` present, writable `/opt`. If `curl`/`wget` is missing, print the exact install command for the detected package manager and exit 30. Then install the pinned `uv` (checksum-verified from `versions.lock.yml`) to `/opt/pinka/bin/uv` and the pinned CPython via `uv python install` into `/opt/pinka/python` (§19.3). Everything after this step uses that interpreter, never the distro's Python.
3. Run preflight. Handle exit codes per §2.4. Interactive confirmation lists disabled features and their research impact.
4. Create `/opt/pinka/ansible-venv` with the pinned Python version via uv and install the pinned `ansible-core` plus Python deps (`jmespath`, etc.) from a lockfile (`bootstrap/requirements.lock`, generated with `uv pip compile --generate-hashes`).
5. Install pinned Ansible collections from `ansible/requirements.yml` into `/opt/pinka/ansible-collections`.
6. Generate `ansible/group_vars/all/versions.yml` from `versions.lock.yml` (or verify it is in sync; fail if drifted).
7. Run `ansible-playbook -i ansible/inventory/hosts.yml ansible/site.yml -e machine=<name> -e @/etc/pinka/host-profile.json` (pass the profile as structured vars under `host_profile`), with vault password handling. The play's first role after `preflight_gate` writes the install record before anything on the host changes (§7.21).
8. Map Ansible-signaled conditions to bootstrap exit codes:
   - `75` — WSL restart required (`wsl --terminate <distro>` on Windows side, then re-run)
   - `76` — reboot required (bare metal/VM), or MOK enrollment pending
   - `77` — operator action required (message printed)
   - `65` — platform release refused: the platform step validates the manifest and the release before any host change (§7.14); on a refusal nothing on the host changed and every error is printed. The code has its own troubleshooting section.
   Use a well-defined mechanism: the role writes `/run/pinka/bootstrap-signal.json` with `{code, message}` and then fails with a recognizable message; bootstrap reads the file.
9. If `--restore`, run `restore.yml`.
10. Run `pinka doctor` and print the summary. Exit with doctor's exit code.
11. Write `/etc/pinka/bootstrap-state.json` with repo commit, tree, dirty flag, ref, timestamp, tags used, overrides, and result.

Re-running bootstrap after any exit (including 75/76/77) must resume cleanly.

### 5.3 Interactive front-end

When bootstrap runs on a TTY without `--no-tui`/`--non-interactive`, it performs only the minimal sanity checks of `prepreflight.sh` (step 2 of §5.2), installs the pinned `uv`, the pinned CPython and the hash-locked installer environment under `/opt/pinka/`, and hands control to the installer TUI (§18). The TUI runs the full preflight, collects every input, writes the configuration files, and then drives the steps of §5.2 by invoking exactly the same commands. If preflight ends in an abort, the TUI offers to remove `/opt/pinka/` again so the host is left untouched. On a host with an install, the TUI also offers uninstall (§5.4, §18.4), running the same command.

There must be **exactly one provisioning code path**: the TUI contains no provisioning logic, and a non-interactive run with the exported answers file must produce an identical result.

### 5.4 Uninstall

`sudo ./bootstrap/bootstrap.sh --uninstall [--dry-run] [--purge-data] [--skip-backup] [--json]` removes what pinka installed; `pinka uninstall` runs the same command from the checkout recorded in `/etc/pinka/bootstrap-state.json`. It works on a partial or failed install and without the `pinka` CLI: if the pinned tooling under `/opt/pinka` is missing, bootstrap installs it first, as a normal run does, and removes it last. The removal runs as an Ansible play (`ansible/uninstall.yml`).

- **Plan.** Computed from the install record (§7.21) and the host's current state, and shown in full before anything is removed; `--dry-run` prints the same list and changes nothing. Items are removed in reverse dependency order: the platform (its `drain` hook, then its units); pinka's units, timers, sockets and slices; the Compose projects with their containers, networks, volumes and images; the Incus objects pinka created (instances in its projects, the projects, profiles, networks, the ACL, trust entries, the pool last; on an adopted Incus only those); the firewall objects pinka added (`inet pinka_filter` and `pinka-nftables.service`, the firewalld zone and ufw rules it added, the `DOCKER-USER` rules for Incus bridges); Tailscale (the serve entries pinka added; a logout only when pinka joined the node); the packages pinka added (holds, repositories and keys first, then the packages, then only those dependencies that were absent at the first install and are no longer needed); the accounts and groups pinka created, and the groups and keys it added to pre-existing accounts; its drop-in files; then pinka's paths, `/opt/pinka` last.
- **What stays.** Everything the record lists as pre-existing stays as it is. The lab's data (`/srv/pinka`, the database files, backups, models) stays unless `--purge-data` is given and the machine name is typed (`--confirm-machine NAME` when non-interactive); a backup (`pinka backup now`) is offered first when a restic repository is configured, unless `--skip-backup`. An NVIDIA driver pinka installed is removed; an enrolled MOK stays in the firmware and is listed as kept, with the `mokutil --delete` steps. Uninstall never reboots; the report says when a reboot is needed.
- **Hosts without an install record** (installed before the record existed): only what carries pinka's names is removed (`pinka-*` units, the reserved accounts with pinka's markers, pinka's paths, Incus objects with pinka's names, `inet pinka_filter`, `pinka-*` repository and key files). Docker, Incus, Tailscale and the NVIDIA packages are kept as "kept: origin unknown (no install record)" unless the owner names them with `--remove-component docker|incus|tailscale|nvidia` after the dry run.
- **Idempotent and resumable.** An item already gone is reported as such; an interrupted uninstall continues when run again.
- **Report.** `/var/log/pinka-uninstall/<UTC-timestamp>.json` (outside the removed paths) lists what was removed, what was kept and why; `--json` prints it.
- **Exit codes** (each with a troubleshooting section): 0 done; 80 uninstall refused or not confirmed, nothing changed; 81 finished with items kept because they could not be removed (named in the report).

---

## 6. Accounts, groups, layout

### 6.1 Groups and accounts (from `group_vars/all/main.yml`)

```yaml
pinka_groups:
  - {name: pinka,            system: false}  # all researchers
  - {name: pinka-admin,      system: false}  # sudo-capable researchers
  - {name: pinka-restricted, system: true}   # restricted data: only accounts of restricted-capable profiles (§12.4)
  - {name: pinka-datasets,   system: true}   # published training datasets: no human members (§7.18)

infra_accounts:
  - {name: pinka-build, groups: []}          # unprivileged platform builds (§7.14)

researchers:                              # names are placeholders; ask the operator
  - {name: researcher1, admin: true,  shell: /bin/bash, keys_file: users/researcher1.pub}
  - {name: researcher2, admin: false, shell: /bin/bash, keys_file: users/researcher2.pub}
  - {name: researcher3, admin: false, shell: /bin/bash, keys_file: users/researcher3.pub}
```

Platform accounts are not listed here. The accounts `<platform>-<service>`, `<platform>-<job>` and `<platform>-migrate` and their groups (socket client groups, `<platform>-db`, membership of `pinka-restricted` for restricted-capable profiles and of `pinka-datasets` for training jobs) are rendered from the active platform's manifest (§7.14, §12). Every derived name is checked against the reserved-name lists and against the accounts and groups already on the host.

Rules:

- Researchers: member of `pinka` and `incus` (Incus's restricted per-user project group). Admins additionally `pinka-admin` with sudo via `/etc/sudoers.d/90-pinka-admin` (validated with `visudo -cf`). **No researcher is ever added to `docker` or `incus-admin`** by default; an explicit `break_glass_docker: [user]` list may exist for admins and is logged loudly.
- Infra and platform accounts: system users, `/usr/sbin/nologin`, home under `/var/lib/pinka/<name>`, no SSH keys, no password.
- `authorized_keys` managed exclusively from `users/*.pub` (`exclusive: true`). Removing a key from the repo removes it from the host.
- Removed researchers (listed under `researchers_removed`) are locked (`usermod -L`, shell `nologin`), not deleted; their data is preserved.
- Pre-existing accounts (§7.21): an account or group with a reserved name that pinka did not create is refused (§12.1). A researcher account that exists before pinka is kept as it is: pinka adds only its groups and its keys from `users/<name>.pub`, never removes a key it did not add (its `authorized_keys` is not managed exclusively), and uninstall removes only what pinka added.

### 6.2 `/srv/pinka` layout (`layout` role)

Base path configurable (`pinka_root: /srv/pinka`). Implement with explicit owner/group/mode and default ACLs where noted. The layout spec must live in one data structure in `group_vars` and be rendered both into tasks and into `docs/architecture.md` (generated table) so docs never drift.

| Path | Owner:Group | Mode | Notes |
|---|---|---|---|
| `/srv/pinka` | root:pinka | 2775 | |
| `platforms/<platform>/` | root:root | 0755 | per platform: `releases/<ref>/` (read-only releases, root:root 0755), `content/<package>/<ref>/` (non-restricted content packages), `state/<service>/<directory>/` (state declared in the manifest, owned by the declaring service) |
| `restricted/<platform>/` | root:pinka-restricted | 0750 | restricted data (§12.4): restricted content packages under `content/<package>/<ref>/`, files 0640 |
| `models/` | root:pinka | 0755 | weights 0444 after verification |
| `models/adapters/` | root:pinka | 0755 | registered adapters, read-only, with lineage (§7.18) |
| `training/datasets/` | root:pinka-datasets | 0750 | published training datasets, read-only, files 0640, each with `PROVENANCE.json` (§7.18) |
| `training/jobs/` | root:root | 0755 | per job run: `<run>/output/` and `<run>/checkpoints/`, owned by the job's account (§7.18) |
| `datasets/` | root:pinka | 2775 | the researchers' shared datasets; default ACL g:pinka:rwX |
| `images/` | root:pinka-admin | 0750 | exported golden image tarballs + sha256 |
| `shared/` | root:pinka | 2770 | default ACL g:pinka:rwX |
| `users/<name>/` | <name>:<name> | 0700 | per researcher |
| `services/` | root:pinka-admin | 0750 | rendered compose/config, never secrets in plaintext world-readable |
| `state/<service>/` | per container UID | 0700 | postgres, prometheus, loki, tempo, grafana data |
| `backups/` | root:root | 0700 | local staging only |

Also `/etc/pinka/` (0755 root) for host profile, bootstrap state, rendered non-secret config and the maintenance window record (§7.20); `/etc/pinka/secrets/` (0711 root:root) for rendered secrets: infra's own secrets root-only, one directory per platform account `/etc/pinka/secrets/<account>/` (0750 root:<account>, files 0640) for the secrets the manifest names, and the database password files `/etc/pinka/secrets/postgres/<role>` (0640 root:<the role's account>; the migrator's readable only by `<platform>-migrate`); `/etc/pinka/platform/` for the active platform's `active.json`, `principals.json`, `endpoints.toml` and per-service configuration (§12.3); `/run/pinka/` (runtime sockets via systemd `RuntimeDirectory=`), including `/run/pinka/platform/<service>/` (platform sockets), `/run/pinka/postgres/` and `/run/pinka/postgres-reader/` (the database sockets, §7.8), `/run/pinka/otlp/` (the agent OTLP socket, §7.9), `/run/pinka/query.sock` (the telemetry query proxy, §7.9), `/run/pinka/gpu.sock` (the GPU arbiter, §7.18) and `/run/pinka/run-network.sock` (the run-network helper, §7.7); `/var/lib/pinka/gpu/` (the arbiter's state and transition log), `/var/lib/pinka/run-networks/` (the run-network helper's operation log), `/var/lib/pinka/platforms/` (deployment records), `/var/lib/pinka/staging/` (root-only staging, 0700), `/var/lib/pinka/git/` (root-only Git mirrors, 0700) and `/usr/local/share/pinka/docs/` (installed documentation releases, §7.19).

---

## 7. Roles (responsibilities and acceptance criteria)

Every role must have: `README.md` (purpose, variables, feature flags consumed, idempotency notes), `defaults/main.yml` with every variable documented, `meta/main.yml`, and tags equal to the role name. Use `ansible.builtin` modules wherever possible; `command`/`shell` only with `creates`/`changed_when`/`failed_when` that make them idempotent.

### 7.1 `preflight_gate`
Re-run preflight, compare with `/etc/pinka/host-profile.json` (§2.6), expose `host_profile` and `features` facts. Fail fast with remediation.

### 7.2 `base`
- Packages are requested by **logical name** (`ca_certificates`, `curl`, `gpg`, `jq`, `git`, `acl`, `btrfs_tools`, `lvm2`, `thin_provisioning_tools`, `nftables`, `time_sync`, `rsync`, `tmux`, `htop`, `iotop`, `sysstat`, `sudo`, …) and mapped to real package names per family in `ansible/roles/base/vars/<family>.yml` (§19.4). Time sync: keep whatever the distro uses (`systemd-timesyncd` or `chronyd`) and only ensure it is enabled and synchronized.
- System timezone **UTC** (researchers may set their own `TZ`).
- journald persistent storage with size limits.
- Automatic updates per family (§19.5): security-only, **never** an automatic reboot, and held packages excluded (Docker, containerd, NVIDIA, Incus, Tailscale, and the kernel on bare metal unless `allow_kernel_updates: true`).
- Version holds per family (§19.5) using the versions from `versions.lock.yml`.
- Third-party repository keys are always imported explicitly after verifying the key fingerprint against `versions.lock.yml` (§19.4). Never `apt-key`, never `zypper --gpg-auto-import-keys`, never `gpgcheck=0`.
- sysctl drop-in (`/etc/sysctl.d/60-pinka.conf`): inotify limits, `net.ipv4.ip_forward=1` (needed by Incus/Docker; firewall handles the rest), documented.

### 7.3 `ssh`
- Drop-in `/etc/ssh/sshd_config.d/10-pinka.conf`: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `AllowGroups pinka`, `PubkeyAuthentication yes`, `X11Forwarding no`, `AllowTcpForwarding local` (researchers need local forwards for Grafana), `ClientAliveInterval 60`.
- Ensure `sshd_config` contains `Include /etc/ssh/sshd_config.d/*.conf` as its first directive (add a managed line if a distro ships without it). The service is `ssh` on Debian-family and `sshd` elsewhere (from family vars).
- Validate with `sshd -t` before reload.
- Exposure is restricted by the firewall role (only `tailscale0` plus optional `ssh_extra_allowed_interfaces`).

### 7.4 `tailscale`
- Source per family from the component source matrix (§19.4): Tailscale's official deb/rpm repositories, or the distro package where Tailscale recommends it (e.g. Arch). Pinned and held.
- Join, when the node is not logged in: `tailscale up --hostname=<machine_name> --advertise-tags=<tag> --accept-dns=false --ssh=false` using an auth key from vault if present; otherwise print the interactive login URL and wait (or fail in `--non-interactive`). The install record notes that pinka joined the node. Skip if already logged in with the same hostname and tags after pinka joined it. A Tailscale that is installed but not logged in is not in use: pinka joins it as above, leaves its package as it is (no repository, pin, hold or replacement), and uninstall logs it out.
- **Adopted node.** A node already logged in before pinka (§2.2, §7.21) is used as it is: no `tailscale up`, no change of hostname, tags or preferences, no repository added and no package pinned, held or replaced. Its hostname, tags, version and preferences are recorded in the install record, and doctor compares with that record instead of the configured hostname and tag. It is refused (preflight `needs_action`, before anything changes) when Funnel is on (invariant 3) or Tailscale SSH is on (it would bypass sshd and the researchers' keys); the remediation names the setting for the owner to change. An adopted node may lack `tag:pinka-lab`, and its key may expire with a personal account: doctor reports both as warnings, not failures, and `docs/security.md` explains the trade-off (the tailnet ACL has to name the node another way, and an expired key takes the lab off the tailnet until the owner signs in again).
- `--accept-dns=false` by default (do not let MagicDNS alter host resolution); configurable.
- Assert Funnel is **off** (`tailscale funnel status`) and fail the play if any funnel config exists. `tailscale serve` for Grafana and for the docs service is optional (`grafana_tailscale_serve`, `docs_tailscale_serve`), tailnet-only. On an adopted node, serve is off by default and needs the owner's explicit consent as well (`tailscale_serve_consent: true`, a checkbox in the installer); pinka records the serve entries it adds, and uninstall removes only those.
- Document required tailnet ACL snippet in `docs/security.md` (only the researchers' identities may reach `tag:pinka-lab:22` and the Grafana and docs serve ports).

### 7.5 `firewall`
- Firewall **backend** is selected per host (§19.6): `nftables` (own table, loaded by a dedicated `pinka-nftables.service` from `/etc/pinka/nftables/pinka.nft`, so no distro nftables config file is edited), `firewalld` (zones), or `ufw` (rules). All three must produce the same effective policy below, verified by doctor. Never flush tables owned by Docker, Incus, firewalld or ufw.
- Input policy: drop by default; allow established/related, loopback, ICMP essentials, `tailscale0` → tcp/22 (+ Grafana serve port if enabled), DHCP/DNS on Incus bridges from instances to the host **only** where needed for Incus's dnsmasq.
- Docker coexistence: Docker sets FORWARD to DROP and loads `br_netfilter`, which breaks Incus bridge traffic. Add a systemd-managed hook (`pinka-docker-incus-compat.service`, `After=docker.service incus.service`) that inserts `DOCKER-USER` ACCEPT rules for Incus bridge interfaces (pattern from `incus_bridge_prefix`). Verify the current recommended approach in Incus docs and implement exactly that. The rules match only the Incus bridges pinka created: they must not change how the traffic of containers that existed before pinka is handled (real-machine test 4.4), and uninstall removes exactly the rules pinka added. Firewall tables and rules of LXD, Podman and the owner's own configuration are never touched.
- Doctor check: list all listening sockets and fail if anything listens on `0.0.0.0`/`::` other than sshd (which is firewalled) — every lab service binds to `127.0.0.1`, a Unix socket, an internal Docker network, or one address of the non-loopback block (§12.11).

### 7.6 `docker` and `nvidia_container_toolkit`
- Docker Engine from the source the component matrix selects (§19.4): Docker's official repositories where Docker publishes them for the distro (Ubuntu, Debian, Fedora, RHEL-compatible), otherwise the distribution's own package (openSUSE, Arch). Required: Engine with Compose v2 plugin and buildx; versions pinned and held where the source allows; the actually installed versions always recorded in the fingerprint.
- **A Docker Engine that existed before pinka** (§2.2, §7.21) is used as it is: pinka never writes its `daemon.json`, never restarts it and never pins, holds or replaces its packages (their versions are still recorded in the fingerprint). It must provide the Compose v2 plugin and buildx; otherwise preflight reports `needs_action` with the packages to install.
- `/etc/docker/daemon.json` (managed header) is written only for a Docker Engine pinka installed itself, and holds only what such an install needs: `live-restore: true`, `data-root` (configurable), `selinux-enabled` on SELinux hosts (§19.7), and `features.cdi: true` only on a GPU host whose Docker, a distribution's own package, is older than 28.2 (below).
- Every other setting the lab needs lives in its own Compose projects, the same on every install: the log driver and size limits per service, the cgroup parent per service (`pinka-services.slice`), and networks with explicit subnets from pinka's pool, which **does not overlap** with Incus bridges, Tailscale CGNAT `100.64.0.0/10`, or common home LANs (`192.168.0.0/16`) — e.g. `172.30.0.0/16` split into /24s; configurable.
- GPU access needs CDI. Docker Engine 28.2 and later enable CDI unless `features.cdi` is `false` (verified in the source of the pinned release), so pinka writes no CDI setting on a Docker of 28.2 or later. When a GPU feature is on and a Docker that existed before pinka has CDI off (`docker info` reports no CDI spec directories), preflight reports `needs_action` with the exact `daemon.json` change for the owner to make (remove `"features": {"cdi": false}`, or on a Docker older than 28.2 add `"features": {"cdi": true}`) and the restart that applies it.
- NVIDIA Container Toolkit (only if `features.gpu_inference` or `features.gpu_training`): from NVIDIA's deb/rpm/zypper repositories or the distro package (Arch), pinned. Use **CDI** for GPU access (`nvidia-ctk cdi generate`, using `--mode=wsl` on WSL2; services request `nvidia.com/gpu=all`) because it works the same on every distro; fall back to `nvidia-ctk runtime configure --runtime=docker` only where CDI is not usable and only on a Docker pinka installed (the fallback writes `daemon.json`), and record which method is active. Restart Docker only if its config changed, and only a Docker pinka installed.
- Verification task: run a pinned CUDA base image **by digest** with `--gpus all` and `nvidia-smi`; record output into `/var/lib/pinka/verify/gpu-docker.txt`.
- Lab services run as Compose projects managed by systemd units: `pinka-core.service`, `pinka-observability.service`, `pinka-inference.service` (`Type=oneshot` + `RemainAfterExit=yes` calling `docker compose up -d --wait` / `down`, or a better pattern if you find one — document it). All images referenced **by digest**. Their containers and the platform's units run in `pinka-services.slice`, pinned to `cpuset_services` when it is set (§12.6).
- Infra's Docker also runs the GPU training containers of §7.18 (names with the prefix `pinka-job-`): the GPU through CDI as for vLLM, and only the fixed options of §7.18, never options from a manifest. The agent never reaches Docker.

### 7.7 `incus`
- Install Incus LTS (6.0.x) from the source the component matrix selects per distro (§19.4): distro package, Zabbly repository (Debian/Ubuntu), EPEL/COPR (RHEL-compatible), OBS (openSUSE Leap), or the Arch package. Verify availability per distro at build time; where only a non-LTS version exists (rolling distros), record it and mark the host `incus_channel: non_lts` in the profile. Group names (`incus`, `incus-admin`) and the `incus-user` daemon availability must be verified per distro and mapped in family vars.
- On Ubuntu 26.04 Incus comes from Ubuntu's own `incus` package in universe (the 6.0 LTS series; Zabbly's `lts-6.0` repository has no 26.04 suite), through a per-release source override (§19.4). Universe has no guaranteed security updates, which the support matrix states; `make lock-pins` reports when Zabbly's `lts-6.0` publishes a 26.04 suite, so the choice is revisited.
- **LXD next to Incus.** LXD (snap or package) is a pre-existing component: detected, never changed, stopped or reconfigured. Before anything is installed, preflight checks that Incus next to it does not clash: bridge names (LXD's networks against `pinka-build0` and the `r-` prefix), their subnets (against pinka's networks and the Docker pool), dnsmasq (a DNS/DHCP server of LXD or the host bound to every address would take the port Incus's dnsmasq needs on its bridges; verify), the firewall (LXD's own tables stay untouched, and pinka's rules must not change LXD's traffic), storage (the pool device or loop path is not LXD's) and the Incus listener's port (LXD's `core.https_address`). On a clash it reports `needs_action` naming the clash, instead of installing.
- Initialize from `incus/preseed.yaml.j2`:
  - storage pool `pinka-targets`: `btrfs` (loop with `incus_pool_size`, or `device`) or `lvm` thin (fallback);
  - **no** default `incusbr0` with NAT for targets; create:
    - `pinka-build0`: NAT enabled, used only by the `build` project for image builds;
    - per-run networks are created and deleted at runtime only through infra's **run-network helper** (below); infra creates the network **template conventions** (prefix `r-`, no NAT, no upstream) and Incus network ACL `pinka-run-default` (deny all egress except intra-network);
  - projects:
    - `build` — image builds, internet allowed;
    - `bench` — official benchmark runs; `restricted=true` with explicit allowances (nesting allowed, VMs allowed only if `features.vm_targets`, limits enforced; `limits.cpu` is the number of CPUs in `cpuset_targets` when it is set, §12.6; `limits.memory` follows the chosen number of concurrent targets, §2.3);
    - personal per-user projects via Incus's `incus` group / `incus-user` daemon (verify mechanism and limits; set conservative per-user limits via `restricted.*` and `limits.*`).
  - profiles: `target-container` (limits template, `security.nesting` per blueprint, root disk size, guest API disabled), `target-vm` (only if `vm_targets`), `build`, `probe-client`.
- Trust and certificates: generate one client certificate per platform service whose profile has Incus access (§12.2), stored in `/etc/pinka/incus/<platform>-<service>/` (0600, owned by the service's account) and added to Incus trust as **restricted**: `controller` to projects `bench` and `build`, `sandbox-operator` and `restricted-operator` to `bench`. Replacing a platform (§7.14) and `rotate.yml` remove the old certificates from the trust store. Prometheus gets a metrics-only certificate (`type: metrics`).
- Enable the Incus HTTPS listener on `127.0.0.1:8443` only (§12.11; for restricted TLS clients and metrics).
- **Run-network helper** `pinka-run-network`: root-run and socket-activated on `/run/pinka/run-network.sock` (0600, owned by the active platform's controller account; root can always connect), reachable only by the controller (peer credentials checked). Bridges live in Incus's `default` project (`bench` has `features.networks=false`), so a certificate restricted to `bench` can create a run network but can neither change nor delete it; run networks are therefore created and deleted only through this helper. Doctor and the platform's integrity check flag any Incus network that neither infra created nor the helper's log records, whatever its name, so a network created outside the helper is caught. It accepts exactly two requests, `create r-<short>` and `delete r-<short>` (`<short>`: 12 lowercase letters and digits), creates the bridge with fixed settings (no NAT, no uplink, ACL `pinka-run-default`) and nothing else, and refuses any other name, request or setting. Every operation (time, request, network, peer, outcome) goes to `/var/lib/pinka/run-networks/operations.jsonl` and to the journal. The platform's `reconcile` hook uses it to remove orphaned run networks.
- Incus lifecycle/exec events: ship a small unit (`pinka-incus-events.service`) that runs `incus monitor --type=lifecycle --type=logging --format=json` (verify flags) and writes JSON lines to journald with a stable identifier, picked up by the collector's journald receiver. This feeds the platform's integrity check ("only the executor touched targets during the agent phase").
- Marker file `/etc/pinka/managed-incus`.

### 7.8 `postgres`
- Container, pinned digest, data in `/srv/pinka/state/postgres`, `initdb` with `--data-checksums`.
- Listen **only** on Unix socket directories, never on TCP (the container has no network): `/run/pinka/postgres/` (group `<platform>-db`, which holds only the platform services that have a database role, plus infra's metrics exporter as `pinka_monitor`; `untrusted-agent` accounts are never members) and `/run/pinka/postgres-reader/` (group `pinka`) for the researchers' reader role and for Grafana, which mounts this directory into its container and connects with its own read-only role. The reader's password file is 0640 root:`pinka`. `pg_hba` cannot tell the two directories apart; the password files decide which role a client can use.
- Bootstrap SQL (idempotent), rendered from the active platform's manifest (§12.5): one database per platform, named after it (the Pokus Platform's is `potka`); login roles `<platform>_<service>` for every service except `untrusted-agent`; `<platform>_migrator` (owner of the schemas, used only by the `migrate` hook); group roles `<platform>_<profile>`, so migrations can grant per profile; `<platform>_reader` (the researchers' read-only role) and `<platform>_grafana`; schemas `public` and `restricted`; `restricted` usage revoked from `PUBLIC`, the reader and the Grafana role. Without a platform there is no platform database. Table-level grants are the platform's responsibility (migrations); infra documents the expectation in `docs/contracts.md`. Infra's own role `pinka_monitor` (for `postgres_exporter`) stays.
- After every `migrate` hook, infra revokes all privileges on schema `restricted` from every role outside the group roles of `controller`, `restricted-operator` and `restricted-reader`, and from the reader and Grafana roles. A grant that a migration made to a forbidden role fails the deployment, and the release does not start (§7.14). A PostgreSQL event trigger that refuses such grants is added only after its coverage of `GRANT` and `REVOKE` on schemas and tables is verified on the pinned release; the revoke and check stay either way.
- Settings: `fsync=on`, `synchronous_commit=on`, `full_page_writes=on` (assert none are disabled), sensible memory for the host (from host_vars), `log_line_prefix` with timestamps.
- `postgres_exporter` for Prometheus.

### 7.9 `observability`
- **OpenTelemetry Collector (contrib)** gateway, pinned digest, listening on `127.0.0.1:4317/4318` (container targets reach it via Incus proxy devices; VM targets reach its listener on `vm_collector_address`, §12.6). Pipelines:
  - logs: `otlp`, `journald` (host) → `memory_limiter` → `resource` → `batch` → Loki (OTLP endpoint, verify Loki version supports native OTLP ingestion) ;
  - metrics: `otlp` → Prometheus (remote-write or Prometheus exporter scraped — choose and document);
  - traces: `otlp` → Tempo.
- **Prometheus**: scrape node_exporter, cAdvisor, Incus metrics (TLS metrics cert), Postgres exporter, OTel collector self-metrics, vLLM `/metrics` (if inference), GPU exporter (DCGM or nvidia-smi exporter, below). Retention configurable by time (default 90d) and size. No listener that a platform profile can reach (query proxy below).
- **Loki** and **Tempo**: single-binary, filesystem storage under `/srv/pinka/state`, retention configured, no listener that a platform profile can reach (query proxy below).
- **Grafana**: provisioned datasources (Prometheus, Loki, Tempo) and dashboards from `config/grafana/dashboards/`: host overview, GPU & vLLM, Incus targets, lab services health. Admin password from vault; researcher Grafana accounts provisioned from `researchers`; anonymous access off; bind `127.0.0.1:18700`; optional `tailscale serve`. Its Prometheus, Loki and Tempo datasources go through the query proxy too, so no Grafana user reaches a write or admin endpoint.
- Exporters: node_exporter and cAdvisor bound to 127.0.0.1 or internal network only.
- **GPU exporter.** `observability_gpu_exporter: dcgm` (where `dcgm_metrics` is available) runs the DCGM exporter as a Compose service of its own: the pinned digest, bound to `127.0.0.1`, the default counters, GPU access through CDI as for vLLM, and no added capabilities, so no profiling metrics. The nvidia-smi exporter then does not start; with `nvidia_smi` the reverse. A render test fails if the choice and the running exporter differ; doctor checks the exporter's endpoint.
- The retention settings of Prometheus, Loki and Tempo enter the expected growth of the requirements computation (§2.3).
- **Platform dashboards**: the dashboards directory the manifest names (JSON files only) is provisioned into a Grafana folder named after the platform, with a read-only PostgreSQL datasource on the platform's Grafana role through the reader socket directory (§7.8). Run dashboards, such as the Pokus Platform's run explorer, ship with the platform, never with infra. The collector turns the manifest's `telemetry.labels` (at most four resource attributes) into Loki labels.
- **Agent OTLP socket** `/run/pinka/otlp/agent.sock`: `pinka-otlp-agent.socket` activates a small proxy to the collector for `untrusted-agent` services, which have no network. The collector stamps everything received through it with `pinka.trust=untrusted`, so the agent cannot pass its telemetry off as another component's.
- **Query proxy** `pinka-query-proxy` (a container in the observability project) is the only way to query telemetry. It listens on `/run/pinka/query.sock` (0660, group `pinka-query`, whose members are exactly the accounts of the profiles that query, §12.2; root can always connect) and forwards to Prometheus, Loki and Tempo only their query endpoints: an allowlist of methods and paths (instant and range queries, series, labels and label values, trace lookup and search), verified against the pinned releases. Everything else is refused with 403, above all push and remote write, deletion, admin, configuration, flush, rules and alerting endpoints. It adds no credentials and does not rewrite queries. Only the collector and the proxy reach Prometheus, Loki and Tempo directly; no platform service reaches their query or push endpoints except through the proxy. The mechanism is decided at implementation and recorded in an ADR, in this order of preference:
  - (a) Prometheus, Loki and Tempo have no listener reachable from the host loopback (an internal Docker network or Unix sockets); if Prometheus must stay on loopback, its web configuration requires client authentication that only the proxy and the collector hold;
  - (b) otherwise, platform accounts get a dedicated UID range, and an nftables output rule drops their connections to the backend ports.

  The reference platform's isolation probe proves the isolation (§7.18, §11), and doctor checks it on every host (§9).

### 7.10 `opa`
- OPA container, pinned digest, bound to `127.0.0.1:8181` (§12.11); loads the active platform's policy bundle read-only from the directory the manifest's `policy` names inside the release, and serves the decision path the manifest declares. Without a platform, or without a `policy`, a deny-all placeholder bundle (`pinka.placeholder`) is loaded and doctor marks OPA healthy-but-placeholder. Decision logs → OTel collector.
- OPA's own authorization policy allows exactly two decision paths: the active platform's, from the manifest, and infra's `pinka.placeholder` (doctor's probe). Everything else, including policy and data writes, is denied; a test proves it.

### 7.11 `vllm` (only if `features.gpu_inference`)
- Compose project `pinka-inference`, pinned `vllm/vllm-openai` digest.
- Serving profiles in `vllm/profiles/<gpu-class>.yml` (e.g. `ada-12g.yml`) define defaults: `--gpu-memory-utilization`, `--max-model-len`, `--max-num-seqs`, dtype/quantization constraints, tool-call parser options, `--enable-lora` limits, `--otlp-traces-endpoint`, `--seed`. Host vars select `vllm_profile`.
- **Deployments.** A deployment spec lives in `/etc/pinka/vllm/deployments/<id>@<version>.yml`, and its file name matches its `id` and `version` fields: `{id, version, model_id (from models.lock.yml), profile, overrides (max_model_len, quantization, max_num_seqs, enable_lora...), adapter_ids, sampling_defaults}`. `adapter_ids` lists adapters as `<name>@<sha256>` and accepts only adapters registered by the GPU arbiter (§7.18). The active one is the symlink `current.yml`, rendered to `/etc/pinka/vllm/current.env` and consumed by the compose project.
- **Bootstrap must end with vLLM actually serving a model** when `features.gpu_inference` is on and a default model was selected (the TUI asks; non-interactive runs use `vllm_default_deployment`). If no model is selected, the unit is installed but disabled, and doctor reports `inference: no deployment` as a warning, not a failure.
- **Serving verification.** After start, wait for `/health`, list `/v1/models`, send one deterministic chat completion and one tool-call request (a dummy `get_time` tool) and assert that a well-formed tool call comes back. Save results to `/var/lib/pinka/verify/vllm-<deployment>.json`. If the tool-call check fails, point to the tool-call parser / chat template configuration as the likely cause.
- Deployments switch only through the GPU arbiter (§7.18): a `serve` request from the platform's controller, or `pinka gpu serve` from an admin, activates a deployment spec. vLLM is stopped while the arbiter trains. The arbiter replaces contract v1's deploy helper.
- Models mounted read-only from `/srv/pinka/models`; `HF_HUB_OFFLINE=1`; bound to an internal Docker network plus `127.0.0.1:18704` (§12.11) for the inference broker; never public (on an inference node the control endpoint forwards it to the tailnet with mutual TLS, §19.10).
- **Who reaches the inference API.** Only the accounts of `inference-broker` and `controller` and infra's own checks (doctor, the serving verification, the health gate, Prometheus' scrape) reach vLLM's API, so no other platform service can bypass the inference broker. The mechanism follows §7.9's order of preference and is recorded in the same ADR: (a) vLLM on a Unix socket (if the pinned release supports it; verify) or behind client authentication, an API key that only those accounts and infra's checks hold; (b) otherwise the UID-range nftables output rule of §7.9, which lets only those accounts through. The same holds for the inference forwarder on a lab node (§12.8). The isolation probe proves it (§7.18).
- Health check and `/metrics` scraped by Prometheus.
- The selected model's memory and disk need (its files plus the serving runtime) enters the requirements computation (§2.3).

### 7.12 `models`
- Input: `models.lock.yml` entries `{id, hf_repo, revision (full commit hash), files: [{path, sha256, size}], license, gated: bool, quantization, notes}`; `size` (bytes, from Hugging Face's file metadata when the model is pinned) feeds the requirements computation (§2.3).
- Download with a pinned `huggingface_hub` CLI in an isolated uv environment into `/srv/pinka/models/<hf_repo>/<revision>/`, verify every sha256, set 0444, write `MANIFEST.json` (id, revision, files, hashes, download time, tool version). HF token for gated models from vault.
- Tag: `models`. Skippable. Never re-download verified files.
- `models/` belongs to root (§6.2). **Adapters** live in `models/adapters/<name>/<sha256>/` with `MANIFEST.json` and `LINEAGE.json`, are registered only by the GPU arbiter (§7.18) and are read-only; base models are mounted read-only into training jobs.
- **Curated starter catalog `models.catalog.yml`.** 3–5 candidate open-weight instruct models with reliable tool calling in vLLM that fit each serving profile (for `ada-12g`: weights + KV cache within 12 GB, using AWQ/GPTQ 4-bit or FP8). Per entry: HF repo, pinned revision, quantization, license, gated flag, expected VRAM, recommended `max_model_len`, tool-call parser, chat-template notes. Research current options at build time and **do not finalize the list without my approval**.
- The TUI offers the catalog entries that fit the detected profile, plus "custom (HF repo + revision)" and "none". A selection is added to `models.lock.yml` with per-file sha256 resolved from Hugging Face file metadata (LFS sha256) *before* download, then verified after download.

### 7.13 `images` (golden image pipeline)
- Each blueprint directory: `image.yaml` (base image: remote, alias, **pinned fingerprint**; instance type container/vm; estimated size in GiB, for the requirements computation (§2.3) until a build measures it; packages with pinned versions where it matters; preloaded Docker images **by digest** for Docker-in-target blueprints; services to enable; telemetry agent config), `cloud-init.yaml`, `provision.yml` (Ansible run inside the build instance), `files/`, `tests/` (post-build assertions).
- Build flow (in project `build`, network `pinka-build0`): launch from pinned base → provision → run tests → **leakage lint** → clean (apt caches, shell histories, logs, cloud-init artifacts, machine-id, SSH host keys regenerated on first boot) → stop → `incus publish` with alias `<blueprint>/<version>` and properties (`pinka.recipe_commit`, `pinka.built_at`, `pinka.base_fingerprint`, and Incus's own `requirements.nesting`, `true` for blueprints that need nesting, so Incus refuses to start such an image without `security.nesting=true`) → `incus image export` to `/srv/pinka/images/<blueprint>/<version>/` with `sha256sum` file.
- Base images from public image servers rotate and old builds disappear: export and keep the **base** image tarball too, so rebuilds stay possible.
- Leakage lint (must fail the build): no files or strings matching scenario ID patterns (`[A-Z]+-[0-9]{3}`), no `fault`, `inject`, `ground_truth`, `scenario`, `pokus`, `pinka` or `potka` strings, with no exceptions (the allowlist of the neutrality lint, §11, does not apply to images), and none of the Pokus Platform's service and unit names, in `/etc`, `/opt`, `/srv`, `/usr/local`, unit names, hostnames, or environment files; no checkouts of the project's repositories inside the image. Make the pattern list configurable.
- Provide at least these blueprint recipes (minimal but real): `_base` (Ubuntu 24.04 + OTel agent + common tooling), `web-nginx-flask` (nginx + gunicorn + Flask app), `pg-primary` (PostgreSQL), `redis-cache` (Redis), `docker-host` (Docker Engine inside target, nesting), `probe-client` (curl, dig, load generator). Each with tests.
- In-target OTel agent config sends to `127.0.0.1:4317` **inside** the instance (reached via proxy device, §12) and must not contain run or scenario identifiers (these are attached host-side).
- Tag: `images`. Skippable.

### 7.14 `platform` (generic platform deployment)

Deploys at most one platform per lab, from its release and its content packages, validated against contract v2 (§12) before anything on the host changes. The role names no platform: everything comes from the manifest, the profiles and the host settings (§8.1). The ground-truth-safe flow of contract v1 (root-only mirror, `git archive`, provenance records) stays and applies to the release and to every content package.

1. **Sources.** `platform: {repo, ref, path, tag_prefix}` and `content: {<package>: {repo, ref, path, tag_prefix}}` from host_vars. Transports: SSH with pinned host keys, anonymous HTTPS, and root-owned `file://` repositories. `path` selects a subdirectory of the repository (empty when the repository holds only the release). Signed tags are verified when `require_signed_tags: true`.
2. **Fetch** into one root-only bare mirror per repository URL under `/var/lib/pinka/git/` (0700); tag and signature checks run there.
3. **Stage** with `git archive <commit> <path>` into `/var/lib/pinka/staging/` (0700). No `.git`; symbolic links that leave the tree are refused.
4. **Validate** the sources and the manifest (§12.1). The release path and the path of every content package from the same repository must not overlap: neither may be a prefix of the other, compared by path segments after normalization, and an empty path (the repository root) overlaps every path. Then the manifest: size limit, safe YAML loader, schema, the reserved-name check against the reserved lists and the host, then the invariant checks. On any error infra deletes the staging directory, prints every error and changes nothing else; bootstrap exits 65. `bootstrap.sh --inspect-platform` stops after this step and prints the summary the installer's Platform review screen shows (§18.4).
5. **Build.** `python-uv`: `uv sync --frozen` with infra's pinned uv, run as the unprivileged account `pinka-build` in a sandbox that may write only the staging directory and the uv cache. `static`: nothing to build. Training job images are pulled by digest at this step, never at job start. Optional tools the manifest requests come from infra's pinned list (today `quarto`: the Quarto CLI from `versions.lock.yml`, which renders HTML and PDF through its bundled Typst engine, no TeX).
6. **Publish** the release to `/srv/pinka/platforms/<platform>/releases/<ref>/` (root:root 0755, read-only for services). Restricted content packages go to `/srv/pinka/restricted/<platform>/content/<package>/<ref>/` only; other content goes to `/srv/pinka/platforms/<platform>/content/<package>/<ref>/`.
7. **Render** from the eight profiles (§12.2): accounts, groups, database roles, Incus certificates, configuration (checked against the release's config schemas), secret files, units (in `pinka-platform.target`), sockets and their ACLs, the principals and endpoints files (§12.3), the OPA bundle link, dashboards, and the CLI link in `/usr/local/bin`.
8. **Switch.** If a release is running: its `drain` hook, then stop. Then the new release's `migrate` hook, the restricted-schema revoke and check (§7.8), an atomic switch of `current`, start, and the `health` hook (hooks: §12.9).
9. **Record** `DEPLOYED_FROM.json` for the release and for each content package, with the platform name, version, runtime and the SHA-256 of the manifest.

A failure in steps 1–7 leaves the running release untouched. A failure in step 8 after a successful drain leaves the platform stopped; migrations are forward-only, so going back means restoring the database and redeploying the previous ref.

**One platform per lab.** `/etc/pinka/platform/active.json` records the active platform's name. A release with another name is refused unless the operator passes `--replace-platform <old name>`. Replacing drains and stops the old platform, removes its units, sockets and CLI link, locks its accounts, makes its database roles `NOLOGIN` and removes its Incus certificates from the trust store. Its state, database, restricted area and releases stay on disk until `pinka platform purge <name>`, which needs a typed confirmation. Running two platforms at once is out of scope, and infra refuses it.

**Hardening baseline** for every platform unit, rendered from the profile and never overridable by a manifest (document every deviation):

```
User=<account>  Group=<account>  SupplementaryGroups=<from the profile only>
NoNewPrivileges=yes  ProtectSystem=strict  ProtectHome=yes  PrivateTmp=yes
PrivateDevices=yes  ProtectKernelTunables=yes  ProtectKernelModules=yes
ProtectControlGroups=yes  RestrictSUIDSGID=yes  LockPersonality=yes
RestrictRealtime=yes  MemoryDenyWriteExecute=yes (unless the relaxation wx-memory)
CapabilityBoundingSet=  AmbientCapabilities=  UMask=0077  Slice=pinka-services.slice
ProtectClock=yes  ProtectHostname=yes  ProtectKernelLogs=yes
ProtectProc=invisible  ProcSubset=pid  RestrictNamespaces=yes
SystemCallArchitectures=native  SystemCallFilter=@system-service
IPAddressDeny=any  IPAddressAllow=<the profile's addresses>
SocketBindDeny=any  (the inference broker's one port excepted)
MemoryMax=<from the manifest's limits>  TasksMax=<from the manifest's limits>
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RuntimeDirectory=pinka/platform/<service>
ReadWritePaths=<declared state>  ReadOnlyPaths=<release, own config>
InaccessiblePaths=<infra internals>
```

- Infra internals are hidden from every profile: the Git mirrors, `/var/lib/incus`, the Docker socket, infra's own secrets and backup credentials, and the sockets of the GPU arbiter and the run-network helper (except for the controller).
- **`untrusted-agent`** additionally gets `PrivateNetwork=yes` and `RestrictAddressFamilies=AF_UNIX`; the lab directories (`/srv/pinka`, `/etc/pinka`, `/var/lib/pinka`, `/run/pinka`) are replaced by empty temporary file systems with only its allowlist bound in (the release read-only, its own state, its own non-secret configuration, its dedicated sockets and the agent OTLP socket); it has no supplementary groups except the client groups of those sockets, and no relaxation. This allowlist replaces contract v1's `InaccessiblePaths` list for the agent.
- The hardening additions (system call filter, `ProtectProc`, `IPAddressDeny`, `SocketBindDeny`) are checked against the real services when implemented; one that breaks a service is reported together with the failing service, never silently dropped.
- Doctor verifies the effective properties of every platform unit via `systemctl show` (§7.17).

### 7.15 `backup`
- restic (pinned binary, checksum-verified). Repository URL and password from vault (destination typically another machine over Tailscale via SFTP or rest-server; ask the operator).
- Timers with `Persistent=true` (the host is not always on):
  - `pinka-pgdump.timer` — `pg_dump -Fc` of the platform database (when the manifest sets `backup.database`) to `/srv/pinka/backups/pg/` (via container), keep N local;
  - `pinka-backup.timer` — restic backup of infra's own directories under `/srv/pinka` (`images`, `datasets`, `shared`, `users`, `backups/pg`; exclude `models/*/` weights by default, `state/prometheus`, `state/loki`, `state/tempo` configurable), plus the platform state directories listed in the manifest's `backup.paths`, the restricted area (`restricted/`), the training datasets (`training/datasets/`) and the adapters with their lineage (`models/adapters/`), and `/etc/pinka` (excluding secrets unless `backup_include_secrets: true` with restic encryption acknowledged). Backups of the restricted area and of the database are written root-only. A platform state directory not in `backup.paths` is not backed up, and doctor lists it so the omission is visible;
  - `pinka-backup-prune.timer` — retention policy from vars.
- `restore.yml`: stops services (the platform's `drain` hook first), restores a chosen snapshot, restores Postgres from the dump, re-imports golden images from exported tarballs (`incus image import`) preserving aliases and verifying fingerprints match the recorded ones, re-verifies model hashes (re-downloading if missing), re-registers adapters and verifies their hashes, restarts services, redeploys the platform release and content refs recorded in their provenance and runs its `health` hook, then runs doctor.
- Backup status metrics exposed to Prometheus (textfile collector).

### 7.16 `lab_session`
- `pinka-lab.target` groups all lab units. Starting the target is the "lab is up" state.
- `pinka-session-start.service` (part of the target, runs once per boot): records a **session record** to `/var/lib/pinka/sessions/<boot_id>.json`: `schema_version`, `boot_id` (`/proc/sys/kernel/random/boot_id`), `started_at`, `fingerprint_hash`, `fingerprint` (the document of §10.2), `preflight_outcome` and `errors`; a field that could not be computed is `null`, with the reason under `errors`. Its JSON Schema is published in `contract/records/`. The platform imports these into its DB (contract).
- Startup gating: platform services must not start until `pinka-health-gate.service` passes: time synchronized, always (NTP synced and, on WSL, wall clock within a threshold of an NTP reference if reachable), plus exactly the facilities the manifest's `requires` lists (`postgres`, `incus`, `telemetry`, `inference`, `opa`, `gpu`), each healthy.
- `pinka-platform.target`, inside `pinka-lab.target`, groups the platform's units; the platform's `reconcile` hook runs as a oneshot unit ordered before them (§12.9).
- Sleep inhibition per provider/power policy (§3.2).
- Ordered shutdown helper `pinka stop` (§9).

### 7.17 `doctor`
- Installs `/usr/local/bin/pinka` (see §9) and doctor check definitions.
- Platform checks come from the profiles and the manifest, never from platform names: the manifest is valid and its hash equals the provenance's; hardening per profile (effective `systemctl show` properties); sockets with owner, mode and ACL, the dedicated agent sockets highlighted, and the sockets the running agent can actually see inside its own mount namespace (anything beyond its allowlist fails); database roles and the closed `restricted` schema; Incus certificates and their projects per profile; restricted content only in the restricted area; the `health` hook; the GPU arbiter's mode and transition log; training-container options; adapters registered with matching hashes; dataset provenance and the split guard; on a `lab` or `inference` node, that training is unavailable on this host.

### 7.18 `gpu_arbiter` and `training` (GPU training jobs)

GPU training is a first-class capability of the research loop: platforms declare training jobs, infra runs them in locked-down containers, and one root-owned arbiter decides whether the GPU serves, trains or idles. Training runs only on `node_role: full` (§19.10).

**The GPU arbiter.**

- `pinka-gpu-arbiter`, root-owned and socket-activated, replaces contract v1's vLLM deploy helper. Its socket `/run/pinka/gpu.sock` (0600) belongs to the active platform's controller account; root reaches it through `pinka gpu`.
- Modes: `idle`, `serving <deployment>`, `training <job run>`; exactly one at a time.
- Requests: `serve <id>@<version>`, `train <job> <submission>`, `idle`, `status`, `publish-dataset`. They come from the controller (peer credentials checked) or from an admin through `pinka gpu status|serve|train|idle`, which runs as root.
- `train` requires no active inference: the arbiter refuses it unless vLLM reports no running and no waiting requests and the controller has declared its sessions drained. It then stops vLLM, runs the job, and afterwards restores the previous deployment when the request asks for it (`gpu_arbiter.restore_after_training` is the default, §8.1).
- Every transition goes to `/var/lib/pinka/gpu/transitions.jsonl` (time, from, to, requester, job or deployment, outcome) and to the journal; the current mode is in `/var/lib/pinka/gpu/state.json`.
- At boot, a job left in `training` is marked `interrupted`. It resumes from its checkpoint directory only if it declares `resumable: true`; otherwise the arbiter restores the previous deployment, or stays `idle`. Without an interrupted job, the arbiter serves the default deployment or stays idle, per `gpu_arbiter.after_boot` (§8.1).
- `pinka stop` and `pinka update-host` ask the arbiter for `idle` after the platform's `drain` hook; a running job is checkpointed and interrupted (`resumable: true`) or ended as failed.

**Jobs.**

- Declared in the manifest's `jobs[]` with profile `gpu-trainer`, the only profile that ever gets GPU devices. The image is pinned by `@sha256:` digest and pulled at deployment (§7.14), never at job start.
- The controller submits a run: job name, base model (from `models.lock.yml`, hashes verified by infra), dataset hashes, hyperparameters and seed. The arbiter computes the job-spec hash from the canonical submission and runs the job as the unit `pinka-gpu-job@<run>` with the container `pinka-job-<run>` in infra's Docker.
- **Container options, fixed by infra and never taken from the manifest:** `--network none`; not privileged; a `--read-only` root file system with a private `/tmp`; `--cap-drop ALL`; `--security-opt no-new-privileges`; Docker's default seccomp profile; no host PID, IPC, network or user namespace; the job's own non-root account `<platform>-<job>` as user, with `pinka-datasets` as its only extra group; the GPU through CDI as for vLLM, WSL2 included; memory, CPU and process limits from the manifest within infra's caps (`training_caps`, §8.1).
- **Mounts**, exactly three kinds: the base model (read-only), the declared datasets (read-only), and `/srv/pinka/training/jobs/<run>/output/` and `checkpoints/` (read-write, owned by the job's account). Nothing else: no restricted data, no sockets, no secrets.
- Timeout and memory come from the manifest within the caps; a timeout ends the run as failed. Jobs checkpoint into their checkpoint directory. Progress goes to the job log and to a metrics file in the output directory.
- `resources.gpus: 0` runs the same flow without the device: the CPU-only variant for hosts without a GPU and for tests. Infra running these jobs in containers does not change the rule that platform services run as systemd units (§12.2).

**Datasets and the split guard.**

- The platform builds a training dataset under a restricted-capable profile, in its own state, and asks the arbiter to publish it (`publish-dataset`).
- The arbiter copies it to `/srv/pinka/training/datasets/<sha256>/` (root:`pinka-datasets` 0750, files 0640, read-only), computes the hash and stores `PROVENANCE.json`: the dataset hash, every included scenario instance with its split, the source runs and the builder's platform commit.
- The arbiter refuses to publish, and refuses to mount, a dataset whose provenance lists any instance in the `test` split, and verifies the dataset hash at mount.
- Infra checks the provenance it is given; that the provenance is complete and truthful is the platform's obligation, tested on the platform side.

**Adapters and lineage.**

- On success the arbiter hashes the adapter and registers it read-only under `/srv/pinka/models/adapters/<name>/<sha256>/`, with `MANIFEST.json` and `LINEAGE.json`: base model hashes, dataset hashes, image digest, job-spec hash, hyperparameters, seed, platform commit, infra commit, start and end times.
- vLLM deployment specs name adapters as `<name>@<sha256>` in `adapter_ids` (§7.11); vLLM loads only registered adapters.
- Adapters with their lineage, and the datasets area, are backed up by default (§7.15): they are research results.

**Topology.** Training runs only on `node_role: full`. On a `lab` or `inference` node the arbiter only serves; the installer and doctor state that training is unavailable on this host, and the documentation records split-node training as future work (§19.10).

**Reference platform.** `tests/reference-platform/` holds `refplat`, a platform written with the Python standard library only (runtime `python-uv` with an empty dependency set; its manifest is the example in §12.1):

- `ctl` (`controller`): the sockets `api` and the dedicated `session`; the four hooks; one table in `public` and one in `restricted`; reads its restricted content package `cases`; builds a dataset from `train`-split cases, publishes it, submits `tune` and imports the lineage.
- `agent` (`untrusted-agent`): uses `session`, then runs an isolation probe that tries every forbidden action and reports what it could do: read the restricted area and content, open the database sockets, connect to a socket not dedicated to it, open any TCP connection, read infra secrets, the Git mirror, the datasets area or the arbiter socket, write outside its state.
- `tune` (`gpu-trainer`): a toy training job that reads its dataset and writes a small adapter-shaped output determined by the seed, with the CPU-only variant for hosts without a GPU.
- **Isolation probe per profile:** for every profile with loopback access (`controller`, `inference-broker`, `sandbox-operator`, `restricted-operator`, `restricted-reader`, `observer`), infra's tests run the probe as a transient unit with that profile's rendered hardening and groups and an account from the platform range. Direct connections to the query and push endpoints of Prometheus, Loki and Tempo must fail for every profile; the query proxy must answer exactly the profiles that query (§12.2); the inference API (vLLM, or the forwarder on a lab node) must answer only `controller` and `inference-broker` (§7.11); the GPU arbiter's and the run-network helper's sockets only the controller. The `agent` probe above covers `untrusted-agent`, and `tune`'s container options cover `gpu-trainer`.

Infra's tests deploy `refplat` and the Pokus Platform's manifest fixture through the same code (§11, §19.9).

### 7.19 `docs` (documentation service)

- `pinka-docs.service` serves an installed documentation release (a docs site tarball, `docs/DOCS_SPEC.md` §B.4) on `127.0.0.1:18706` (§12.11). It is off while `docs_release` is null. It runs with `DynamicUser=yes`, read-only access to the docs directory and the hardening baseline of §7.14; it is the only docs server the lab ships.
- Versions live in `/usr/local/share/pinka/docs/<version>/` with a `current` symbolic link that switches in one step. Only root installs them: `pinka docs install <tarball> --sha256 <sum>` verifies the checksum, unpacks into a new version directory (refusing absolute paths, `..` and links that leave it), switches `current` and keeps the three most recent versions; `pinka docs rollback` switches `current` back to the previous version. Bootstrap installs `docs_release` the same way.
- `docs_tailscale_serve: true` exposes the service through `tailscale serve`, tailnet-only (§7.4); otherwise researchers use SSH port forwarding.
- `pinka status` and doctor show the served version.

### 7.20 Maintenance windows (staging on shared hardware)

A QA maintenance window lets a staging lab use the production lab's hardware for a fixed time. A staging host is a separate OS instance with its own `/srv/pinka` (§13, invariant 10), so each side keeps its own record and the admin opens the window on both.

- The record `/etc/pinka/maintenance-window.json` (root:root 0644) holds only `start`, `end` and `created_at` (UTC). `pinka window open --start <time> --end <time>` writes it; `pinka window close` removes it.
- **Production:** `window open` first drains and stops the lab exactly like `pinka stop` (the `drain` hook, then the GPU arbiter's `idle`). While a record exists, `pinka start` refuses, the health gate fails (so the lab does not start at boot either), and doctor reports the open window. When the end passes, production stays stopped until an admin runs `pinka window close`: it never restarts on its own while staging may still be running.
- **Staging** with `staging_shared_hardware: true`: `pinka start` refuses unless its own record is open now, the GPU is idle (no process uses it) and every configured port (§12.11) is free. When the end passes, `pinka-window-expiry.timer` stops the staging lab, and `start` refuses again.
- Staging on separate hardware needs no window.

### 7.21 `install_record` (pre-existing components)

Runs right after `preflight_gate`, before any change to the host.

- Writes `/var/lib/pinka/install-record.json` (0600 root; its schema ships with the role): for every item pinka manages — the packages of the component source matrix and of every role, repository files, keys, holds, accounts and groups (and the groups and keys pinka adds to pre-existing accounts), units, firewall objects, drop-in files, and the state of Docker, Incus, LXD, Podman, Tailscale (joined or not, preferences, serve entries) and the NVIDIA driver — whether it existed before pinka.
- The `preexisting` part is written once, at the first install, and never rewritten, so a later run never mistakes pinka's own items for the host's. Every run adds what it created to the `added` part (new platform accounts, new packages, serve entries). At the first install the record also keeps the full package list with versions and the enabled units, for uninstall's dependency cleanup and the snapshot lane (§19.9).
- Roles read the record through the facts it exposes and never change what it lists as pre-existing (invariant 24); preflight has already stopped on a host where the lab could not run without such a change (§2.2).
- An NVIDIA driver installed before pinka is never replaced or removed; one pinka installed is removed by uninstall (§5.4).
- Check mode writes nothing and prints the record a real run would write.

---

## 8. Configuration model

### 8.1 `host_vars/<machine>.yml` (example to ship, fully commented)

```yaml
machine_name: pinka-lab
lab_mode: production               # production | staging (QA environments; see §13, invariant 10)
staging_shared_hardware: false     # staging only: on production's hardware, start needs an open maintenance window (§7.20)
lab_provider: auto                 # auto | baremetal | wsl2 | vm (auto = from host profile; explicit must match)
power_policy: lab_session_inhibit  # never_sleep | lab_session_inhibit | os_default (baremetal/vm only)

expected:                          # doctor warns if effective values differ (important on WSL)
  memory_gib: 22
  cpu_threads: 20

cpuset_services: "0-5"             # lab services pinned here (pinka-services.slice)
cpuset_targets: "6-19"             # Incus targets pinned here; the platform gets it from endpoints.toml (§12.6)

incus_pool_source: loop            # loop | device
incus_pool_size: 300GiB            # for loop
incus_pool_device: ""              # /dev/disk/by-id/... for device
incus_pool_device_confirm: ""      # must equal incus_pool_device exactly, or nothing is formatted
incus_concurrent_targets: 2        # targets that run at once: sizes the bench project's limits.memory and the memory requirement (§2.3)

pinka_root: /srv/pinka
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

platform:                          # null = standalone lab without a platform (§7.14)
  repo: "git@github.com:example-org/pokus-platform.git"   # SSH, anonymous HTTPS or file://
  ref: "platform-v0.1.0"
  path: "platform/"                # subdirectory of the release in the repository ("" = the root)
  tag_prefix: "platform-v"
content:                           # one source per content package the manifest declares
  catalog: {repo: "git@github.com:example-org/pokus-platform.git", ref: "catalog-v0.1.0", path: "catalog/", tag_prefix: "catalog-v"}
require_signed_tags: false         # default; true verifies SSH-signed tags against the committed allowed-signers file
platform_settings: {}              # per service; checked against the service's config schema
gpu_arbiter:
  after_boot: serve                # serve (the default deployment) | idle
  restore_after_training: true
training_caps: {timeout_seconds: 86400, memory_max: 24G, cpus: 8}   # jobs' limits must stay within these

grafana_tailscale_serve: false
docs_release: null                 # {url: "<docs release tarball URL or path>", sha256: "<hex>"}; null = no docs service (§7.19)
docs_tailscale_serve: false        # expose the docs service through tailscale serve (tailnet only)
tailscale_serve_consent: false     # adopted Tailscale node only: consent to tailscale serve for Grafana or docs (§7.4)
observability_prometheus_retention: 90d      # retention settings enter the expected growth (§2.3)
observability_prometheus_retention_size: 0   # 0 = by time only
observability_loki_retention: 2160h
observability_tempo_retention: 720h
ports: {}                          # overrides of the default ports (§12.11); the effective values go to endpoints.toml
vm_collector_address: 198.18.0.1   # VM targets' collector address (§12.6); must not overlap any host address or route
ssh_extra_allowed_interfaces: []   # e.g. [enp5s0] to allow LAN SSH on bare metal
```

### 8.2 Secrets (`group_vars/all/vault.yml`, Ansible Vault encrypted)

Keys (document in `vault.yml.example`): `vault_postgres_passwords.<role>`, `vault_grafana_admin_password`, `vault_grafana_user_passwords.<user>`, `vault_restic_repository`, `vault_restic_password`, `vault_tailscale_authkey` (optional), `vault_hf_token` (optional), `vault_git_deploy_keys.<source>` (optional, one per platform or content source), `vault_platform_secrets.<service>.<name>` (the secrets the manifest names).

Rules:
- Every task handling secrets uses `no_log: true`.
- Rendered secret files: 0600/0640 with explicit owners; never under world-readable paths; never in container image layers.
- **Per-machine identities are generated on the host, never stored in the repo or vault**: SSH host keys, Tailscale node identity, Incus server and client certificates, the active platform's run-manifest signing key (`/etc/pinka/signing/<platform>/` ed25519, 0400 for its controller, generated once; its public key is published to `/srv/pinka/shared/signing-keys/<platform>-<machine>-<date>.pub` and kept forever so old runs remain verifiable).

---

## 9. Operator CLI: `pinka`

Python (stdlib-only preferred so it works even when uv/venv is broken), installed to `/usr/local/bin/pinka`. Subcommands:

| Command | Purpose |
|---|---|
| `pinka preflight [--json] [--plan FILE]` | Run preflight (read-only); with `--plan`, the requirements of those choices (§2.3) |
| `pinka doctor [--json] [--smoke]` | Full health and security check (below) |
| `pinka status` | Units, containers, Incus projects/instances summary, GPU and the arbiter's mode with its last transitions, disk, last backup; the platform's name, version and `health` answer; the served docs version; an open maintenance window; on a `lab` or `inference` node, that training is unavailable on this host |
| `pinka start` / `stop` | Start/stop `pinka-lab.target`. `start` waits for the platform's `health` hook and refuses while a maintenance window is open on production, or outside one on shared-hardware staging (§7.20). `stop` first runs the platform's `drain` hook (if a platform is active) with `--timeout`, then asks the GPU arbiter for `idle`, then stops `pinka-platform.target` and the remaining units in reverse dependency order |
| `pinka fingerprint [--json]` | Print the current environment fingerprint and its hash. As root it computes and caches the fingerprint; as any other user it prints the cached root-computed fingerprint with its `computed_at` time and never escalates |
| `pinka backup now` / `backup list` | Trigger backup / list snapshots |
| `pinka restore` | Wrapper around `restore.yml` |
| `pinka uninstall [--dry-run] [--purge-data] [--skip-backup] [--json]` | Run `bootstrap.sh --uninstall` from the recorded checkout (§5.4) |
| `pinka images list`, `images build <blueprint>`, `images export` | Golden image helpers |
| `pinka user add`, `user remove`, `user keys` | Thin wrappers that edit repo files and remind to commit + re-run bootstrap (never mutate the host directly) |
| `pinka wsl-helpers` | Print paths and instructions for `contrib/windows/` (wsl2 provider only) |
| `pinka gpu` `status`, `serve`, `train`, `idle` | The GPU arbiter's requests for admins (root only, through the arbiter, §7.18) |
| `pinka platform purge <name>` | Remove a replaced platform's state, database, restricted area and releases after a typed confirmation (§7.14) |
| `pinka docs install <tarball> --sha256 <sum>`, `docs rollback` | Install a documentation release, keeping three versions, or switch back to the previous one (root only, §7.19) |
| `pinka window open --start <time> --end <time>`, `window close` | Open or close a maintenance window (root only, §7.20) |

### Doctor checks (each: id, description, severity, result, remediation)

- Preflight re-run outcome and drift vs host profile.
- Units in `pinka-lab.target` active; containers healthy; images running match pinned digests.
- No unexpected listening sockets (non-loopback: only sshd, reachable via the allowed interfaces only, and the non-loopback block of §12.11); every configured port is held by its lab listener.
- nftables `inet pinka_filter` present; DOCKER-USER compat rules present.
- Tailscale: logged in, **Funnel off**, Tailscale SSH off; hostname and tags as configured on a node pinka joined, as recorded on an adopted node (§7.4), where a missing `tag:pinka-lab` and an expiring key are warnings, not failures.
- Docker GPU test (if `gpu_inference` or `gpu_training`) and CDI enabled in Docker (§7.6); the driver supports the CUDA version of the pinned vLLM image (§2.2), with the remediation; vLLM health (if a deployment is active).
- Incus: pool backend and free space, projects, profiles, restricted certs present with correct ownership, metrics endpoint scrapeable, events unit active.
- `/srv/pinka` tree ownership/modes/ACLs match the layout spec exactly.
- Researchers not in `docker`/`incus-admin` (except break-glass list); service accounts not in `docker`.
- Platform checks from the profiles and the manifest (§7.17), if a platform is active.
- Postgres: infra's and the platform's roles exist; `restricted` schema not readable by the reader and Grafana roles; fsync/synchronous_commit on.
- Observability round-trip: emit a test log record via OTLP with a unique ID and confirm through the query proxy that it is queryable in Loki within N seconds; same for a test span in Tempo and a test metric in Prometheus.
- Query proxy: a query through `/run/pinka/query.sock` succeeds; a push, a remote write, a delete and an admin request are refused; a platform account reaches neither the query nor the push endpoints of Prometheus, Loki and Tempo directly.
- CPU sets: `cpuset_services` and `cpuset_targets` valid and disjoint, applied to `pinka-services.slice` and the `bench` project; a warning when SMT siblings of one core fall into different sets.
- Docs service: the served version. Maintenance window: open or not, with its end.
- Time sync state and clock offset.
- Free space per mount point, recorded on every run, with its trend over the last runs: a warning at 15 % free, or when the trend reaches full within 7 days; the requirements of the configured choices (§2.3) still met.
- The install record present and readable (§7.21).
- The GPU exporter the configuration chose answers on its endpoint (§7.9).
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
  vllm:            {image: "docker.io/vllm/vllm-openai", tag: "<verify>", digest: "sha256:<verify>", cuda: "<verify>"}   # cuda: the CUDA version the image is built on (§2.2)
  cuda_base_test:  {...}
  refplat_job:     {...}     # the reference platform's training job image (tests); platform job images are pinned in their manifests, not here
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
- A package version may differ per architecture: `packages.<component>.<release>.<package>` is a version or `{x86_64: <version>, aarch64: <version>}` (Zabbly builds each architecture separately); the play picks the host's.
- Each container image records its compressed size per platform from its manifest (`size`), re-verified by `make lock-check`; the requirements computation (§2.3) estimates the unpacked size as 2.5 × that until the image is pulled on a host.
- `make lock-pins` (maintainer tooling) fetches and prints the vendor pins the lock cannot verify from its own data, and reports when Zabbly's `lts-6.0` repository publishes a suite for a release that takes Incus from elsewhere (Ubuntu 26.04, §7.7).

### 10.2 Environment fingerprint

Generated by `pinka fingerprint` and at every session start. `pinka fingerprint --json` prints one document: `fingerprint_schema_version`; `hash`, written `sha256:<hex>`, over the canonical JSON of `fingerprint` (sorted keys, no whitespace, UTF-8); `fingerprint`, the hashed fields below; and `computed_at` and `boot_id`, which are recorded but not hashed. Its JSON Schema is published in `contract/records/`. The hashed fields:

- host profile essentials: provider, environment class, hypervisor, OS id/version, kernel release, arch, support tier, overrides;
- hardware: CPU model, threads, memory, GPU name, VRAM, driver version, CUDA/compute capability, GPU path (`wsl-dxg`/`native`/`passthrough`);
- software: actual installed versions of Docker, containerd, NVIDIA toolkit, Incus, Tailscale, Python, ansible-core; running container image digests; golden image fingerprints by alias;
- config: `pokus-infrastructure` commit + dirty flag, `versions.lock.yml` sha256, host_vars sha256; the active platform's name, version, manifest sha256 and content refs; the adapter ids of the served deployment;
- WSL extras: `wsl.conf` effective values, kernel string (Windows build is unknowable from Linux with interop off — record `null` and document).

Volatile fields (`computed_at`, `boot_id`) are recorded alongside but excluded from the hash.

Document in `docs/reproducibility.md` that latency/resource comparisons are valid only within the same fingerprint hash (or a documented compatible subset), while accuracy comparisons require identical model + serving profile + agent config.

---

## 11. Testing and quality

- **Preflight unit tests** (pytest; also runnable with plain `python3 -m unittest` since preflight is stdlib-only): fixture roots under `preflight/tests/fixtures/<name>/` containing fake `/etc/os-release`, `/proc/...`, `/sys/...`, `/dev` markers, and a `commands.json` mapping command lines to stdout/exit codes. Required fixtures, each with an expected outcome/provider/features assertion:
  - `ubuntu2404-baremetal-nvidia-driver-loaded` → supported, baremetal, gpu_inference, vm_targets, dcgm
  - `ubuntu2404-baremetal-nvidia-no-driver-secureboot` → needs_action (MOK; `auto` without consent)
  - `ubuntu2404-baremetal-nvidia-no-driver-secureboot-consent` → degraded, planned action `secure_boot_mok` (`auto` with consent)
  - `ubuntu2404-baremetal-nvidia-no-driver-secureboot-manual` → needs_action (MOK; `manual`, consent has no effect)
  - `ubuntu2404-baremetal-nvidia-no-driver-secureboot-skip` → degraded (no gpu_inference)
  - `ubuntu2404-baremetal-no-gpu` → degraded (no gpu_inference)
  - `ubuntu2404-baremetal-nvidia-old-driver` and `ubuntu2404-wsl2-gpu-old-driver` → degraded (no gpu_inference: the driver's CUDA version is below the pinned vLLM image's; the remediation names the driver update, on WSL2 on Windows)
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
  - `debian13-baremetal` → supported tier
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
  - `rhel8-cgroup-v1` → unsupported (the explicit RHEL-compatible 8 row; the reason names the kernel argument `systemd.unified_cgroup_hierarchy=1` and recommends release 9 or 10)
  - `fedora-selinux-enforcing-firewalld` → supported path with backend firewalld and mac selinux recorded
  - `ubuntu2404-ufw-active` → backend ufw recorded, no conflict
  - `ubuntu2404-aarch64-no-gpu` → experimental arch, degraded (no gpu_inference)
  - `ubuntu2404-no-btrfs-no-dmthin` → unsupported (no quota-capable storage)
  - `ubuntu2404-cgroup-v1` → unsupported
  - `ubuntu2404-low-memory` → unsupported (below required minimum)
  - conflicting-signal fixture → confidence `low` + warning
  - `ubuntu2604-baremetal` → supported, pending verification noted
  - `ubuntu2404-baremetal-lxd` → Incus next to LXD without a clash: info; `ubuntu2404-baremetal-lxd-clash` → needs_action naming the clash
  - `ubuntu2404-baremetal-podman` → info; `ubuntu2404-baremetal-podman-docker` → needs_action
  - `ubuntu2404-baremetal-tailscale-adopted` → info, the node in `components`; `ubuntu2404-baremetal-tailscale-funnel` and `ubuntu2404-baremetal-tailscale-ssh` → needs_action
  - `ubuntu2404-baremetal-docker-preexisting` → info; with a GPU and CDI off → needs_action with the `daemon.json` change
  - memory just above and just below the floor and the computed required and recommended values (with the 0.94 tolerance), and a disk shortfall listed item by item
- Snapshot tests for the human-readable summary.
- Schema validation tests for every generated host profile.
- **Lint**: `shellcheck`, `shfmt -d`, `ruff`, `ruff format --check`, `mypy --strict` for preflight and CLI, `yamllint`, `ansible-lint` (production profile), JSON Schema validation of `support-matrix.yml` and `versions.lock.yml`.
- **Ansible**: `ansible-playbook --syntax-check` for all playbooks; check-mode tests for provider-agnostic roles (`base`, `users_groups`, `layout`, `ssh`) in a disposable test environment. Provide `make test-roles` that runs these roles inside a throwaway Incus system container or VM **only when explicitly invoked**, and document that hardware-dependent roles (GPU, Incus-in-Incus storage) are validated by doctor on real hosts.
- **Docs drift**: CI regenerates `docs/support-matrix.md` and the layout table and fails if they differ from committed versions.
- **Contract v2 tests**: manifest schema and invariant tests with one invalid fixture per rule (unknown field, reserved name per namespace, second controller, agent on a non-dedicated or shared socket, agent relaxation, job without digest or with another profile, path escape, limits over cap, release and content package paths overlapping in one repository); rendering golden files (units, sockets, ACLs, accounts, groups, SQL, Incus certificate requests, training-container arguments) for the reference platform, the Pokus Platform's manifest fixture and a fixture using all eight profiles, with `systemd-analyze verify` on every unit; the restricted-schema revoke and check against a real PostgreSQL; GPU arbiter state machine (fake Docker, fake vLLM), training-container option, split-guard and lineage tests; hooks and `pinka` commands against a fake host; and the reference platform (§7.18).
- **Neutrality lint** (`make lint-neutral`, part of `make lint`): infra's roles, tools and templates contain no `potka` and none of an explicit list of the Pokus Platform's service and unit names (its accounts and services, `orchestrator`, `agent-runner`, `gt`, `catalog`, its unit names), matched as whole tokens, so a listed name inside a longer name never matches. `pokus` appears only in an explicit allowlist: the repository names `pokus-infrastructure` and `pokus-platform` and the display names "Pokus", "Pokus Infrastructure" and "Pokus Platform". Test fixtures, docs and the reserved-name list are excluded. The allowlist applies to this lint over repository code only: target images (§7.13), the platform's trace-cleanup scan and its ticket lint allow no `pokus`, `pinka` or `potka` string at all.
- **Query proxy tests**: every allowlisted query path passes; push, remote write, delete, admin, configuration and flush requests are refused. Required: the isolation probe, run for every loopback-capable profile (§7.18), proves that a platform service reaches neither the query nor the push endpoints of Prometheus, Loki and Tempo directly, only the proxy, and that only `controller` and `inference-broker` reach the inference API; doctor checks the same on a real host.
- **Run-network helper tests**: create and delete with the fixed settings; refusal of another name, request, setting or peer; the operation log; reconcile removing an orphaned network; doctor flagging a network that neither infra created nor the helper's log records, whatever its name (fake Incus).
- **Records**: the session record and the fingerprint document validate against their schemas in `contract/records/`, including records with `null` fields and their reasons.
- **Ports, docs service and maintenance windows**: port overrides reach `endpoints.toml`; preflight fixtures for a lab port in use and a conflicting VM collector address; `pinka docs install` and `rollback` (checksum, unsafe archive members, three versions kept) and `pinka window` (open, close, expiry on both sides, the shared-hardware checks) against a fake host.
- **Check mode in CI**: the required job runs the provider-agnostic roles (§19.9); a separate, non-blocking extended job runs the whole play in check mode with the reference platform enabled and lists every skipped component.
- **Install record and uninstall**: the record's first-install and later-run parts; every role's refusal to change a pre-existing component; the uninstall plan from recorded fixtures (order, kept items, a host without a record, `--remove-component`), its report and exit codes; the `make test-distros` uninstall lane and its pre-existing variant (§19.9).
- **Requirements**: the computation from `preflight/requirements.yml` for every combination of choices the installer offers; preflight, the installer and doctor give the same numbers for the same choices.
- **Python versions**: preflight's suite also runs on the Python of the newest supported distribution (Ubuntu 26.04: 3.14), besides 3.9 and the pinned CPython.
- **CI** (GitHub Actions): all of the above except role tests that need privileged virtualization.

---

## 12. Integration contract v2: platforms plug in through a manifest

Write this into `docs/contracts.md` and treat it as a versioned interface (`contract_version: 2`). Infra publishes it with every release together with `contract/`: the manifest's JSON Schema, the profile definitions, the reserved-name lists and, in `contract/records/`, the schemas of the provenance and lineage records, the session record (§7.16) and the fingerprint document (§10.2). They change only with a contract version bump (§0). A platform pins an infra release and plugs in through one manifest; infra turns the manifest into accounts, units, sockets, containers and grants, and no infra code names a particular platform (neutrality lint, §11). The Pokus Platform (`potka`) is the first platform; the reference platform `refplat` (§7.18) proves that a second one fits without infra changes. What is specific to one platform (its services, their sockets and endpoints, its agent rules, its attestation format and official check) belongs to that platform's own specification.

### 12.1 The manifest

Every platform release carries one file, `platform-manifest.yaml`, at its root. Infra validates it against `contract/platform-manifest.schema.json` (JSON Schema draft 2020-12; unknown fields are errors) and refuses the release before anything on the host changes (bootstrap exit code 65, §7.14).

| Field | Required | Content | Infra checks beyond the schema |
|---|---|---|---|
| `contract_version` | yes | `2` | equals the contract version of the installed infra |
| `name` | yes | platform name, 2–12 lowercase letters and digits | not a reserved platform name; every name derived from it is checked against the reserved lists |
| `version` | yes | SemVer of the release | — |
| `runtime` | yes | `python-uv` or `static` | `python-uv` needs `uv.lock` at the release root |
| `requires` | no | infra facilities: `postgres`, `incus`, `telemetry`, `inference`, `opa`, `gpu` | the health gate checks exactly these |
| `services[]` | yes | `name`, `profile` (any profile except `gpu-trainer`), `start` (command inside the release), optional `after`, `state`, `config`, `secrets`, `limits`, `relaxations` | exactly one `controller`; at most one `inference-broker`; commands are regular files inside the release; limits within infra's caps |
| `jobs[]` | no | GPU training jobs: `name`, `profile` (`gpu-trainer`), `image` (OCI reference pinned by `@sha256:` digest), `command`, `inputs` (a base model, dataset kinds), `outputs` (an adapter name), `resources` (`gpus` 1, or 0 for the CPU-only variant; memory; CPUs), `timeout_seconds`, `resumable` | profile must be `gpu-trainer`; a digest is required; timeout and memory within infra's caps; the only output kind is an adapter |
| `sockets[]` | no | `name`, serving `service`, `clients` (profiles, the human classes `researchers` and `admins`, and `root`), `untrusted_clients` | the connection rules of §12.3 |
| `hooks` | `health` yes, others no | `migrate`, `drain`, `health`, `reconcile`: a command and `timeout_seconds` each | timeouts within caps; run under fixed accounts (§12.9) |
| `backup` | no | `paths` (state directories to back up) and `database` (whether the platform database is backed up) | every path is a declared state directory |
| `content[]` | no | content packages deployed from their own sources: `name`, `restricted`, `required` | restricted packages go to the restricted area only; a package's source path never overlaps the release path in the same repository (§7.14) |
| `docs` | no | documentation directory in the release | inside the release |
| `policy` | no | OPA bundle directory and decision path | path pattern; bundle loaded read-only |
| `dashboards` | no | Grafana dashboards directory | JSON files only; read-only datasource |
| `tools` | no | optional pinned tools from infra's list (today: `quarto`) | name in the list |
| `cli` | no | name and command of the researchers' CLI, linked into `/usr/local/bin` | not reserved, not an existing command |
| `telemetry.labels` | no | up to 4 resource attributes the collector turns into Loki labels | attribute-name pattern |

The only relaxation in the fixed list is `wx-memory` (turns off `MemoryDenyWriteExecute`, which Quarto needs); it is never allowed for `untrusted-agent`. Every path in the manifest is relative and normalized; after extraction infra also refuses symbolic links that leave the release.

**Sources in one repository.** A content package may live in the same repository as the release (the Pokus Platform keeps `platform/` and `catalog/` side by side). Infra then refuses the deployment (exit 65) when the release path and the package's path overlap, that is when one is a prefix of the other by path segments; an empty path, the repository root, overlaps every path. Otherwise a restricted package could be extracted as part of the release.

**Reserved names.** The schema carries one list per namespace, generated from infra's own data, so a new infra name is reserved automatically. The validator derives every name the manifest would create and refuses one that is reserved or already exists on the host. Platform objects live under platform subtrees (`/srv/pinka/platforms/<platform>/`, `/run/pinka/platform/`, `/etc/pinka/platform/`), so path collisions can come only from the segments listed.

| Namespace | Reserved |
|---|---|
| Accounts and groups | `root`, `pinka`, `pinka-admin`, `pinka-restricted`, `pinka-build`, `pinka-datasets`, `pinka-query`, `pinka-lab`, `pinka-monitor`, and every account or group already on the host |
| systemd units, targets, slices | `pinka-lab.target`, `pinka-platform.target`, `pinka-services.slice`, `pinka-core`, `pinka-observability`, `pinka-inference`, `pinka-health-gate`, `pinka-session-start`, `pinka-sleep-inhibit`, `pinka-incus-events`, `pinka-inference-control`, `pinka-gpu-arbiter`, `pinka-gpu-job@`, `pinka-hook@`, `pinka-otlp-agent`, `pinka-backup`, `pinka-backup-prune`, `pinka-pgdump`, `pinka-security-patch`, `pinka-nftables`, `pinka-docker-incus-compat`, `pinka-vm-otlp-address`, `pinka-docs`, `pinka-window-expiry`, `pinka-run-network`, `pinka-inference-forwarder` |
| Containers | `pinka-postgres`, `pinka-postgres-exporter`, `pinka-prometheus`, `pinka-loki`, `pinka-tempo`, `pinka-grafana`, `pinka-opa`, `pinka-otel-gateway`, `pinka-otel-journald`, `pinka-node-exporter`, `pinka-cadvisor`, `pinka-gpu-exporter`, `pinka-vllm`, `pinka-query-proxy`, and the prefix `pinka-job-` |
| Networks and Incus | `pinka-build0`, `pinka-otlp0`, `pinka-run-default`, projects `bench` and `build`, run networks `r-*` |
| PostgreSQL | roles `postgres`, `pinka_monitor`, `pg_*`; databases `postgres`, `template0`, `template1` |
| Path segments | under `/srv/pinka`: `models`, `datasets`, `images`, `shared`, `users`, `services`, `state`, `backups`, `platforms`, `restricted`, `training`; under `/etc/pinka`: `secrets`, `incus`, `inference`, `platform`, `qa`, `signing`, `vllm`, `gpu`, `postgres-reader`; under `/var/lib/pinka`: `git`, `staging`, `sessions`, `fingerprint`, `gpu`, `platforms`, `run-networks`; under `/run/pinka`: `postgres`, `postgres-reader`, `platform`, `otlp`, `gpu.sock`, `query.sock`, `run-network.sock` |
| Commands | `pinka`, `pinka-preflight`, and every command already on the `PATH` |
| Platform names | `infra`, `lab`, `root`, `system`, `platform`, `restricted`, `training`, `pinka`, `pokus` |

Example, the reference platform (§7.18):

```yaml
contract_version: 2
name: refplat
version: 0.1.0
runtime: python-uv
requires: [postgres, telemetry, gpu]
services:
  - name: ctl
    profile: controller
    start: [.venv/bin/refplat-ctl]
    state: [records, datasets]
    config: schemas/ctl.schema.json
    secrets: [api-token]
  - name: agent
    profile: untrusted-agent
    start: [.venv/bin/refplat-agent]
    after: [ctl]
jobs:
  - name: tune
    profile: gpu-trainer
    image: docker.io/library/python@sha256:<digest verified at implementation>
    command: [python3, -I, -c, "<toy training script>"]
    inputs: {base_model: true, datasets: [train]}
    outputs: {adapter: refplat-tune}
    resources: {gpus: 1, memory_max: 4G, cpus: 2}
    timeout_seconds: 600
    resumable: true
sockets:
  - {name: api, service: ctl, clients: [researchers, root]}
  - {name: session, service: ctl, clients: [untrusted-agent], untrusted_clients: true}
hooks:
  migrate:   {run: [.venv/bin/refplat-admin, migrate], timeout_seconds: 300}
  reconcile: {run: [.venv/bin/refplat-admin, reconcile], timeout_seconds: 120}
  drain:     {run: [.venv/bin/refplat-admin, drain], timeout_seconds: 600}
  health:    {run: [.venv/bin/refplat-admin, health], timeout_seconds: 30}
backup: {paths: [ctl/records], database: true}
content:
  - {name: cases, restricted: true, required: true}
docs: docs/
cli: {name: refplat, run: [.venv/bin/refplat]}
```

The Pokus Platform's manifest has the same shape, with its services, its training job and the scenario catalog as its one restricted content package; it lives in the platform's repository, and infra's tests keep a copy as a fixture.

### 12.2 Privilege profiles

Each service or job names exactly one of eight profiles, and its privileges are exactly that profile's grants. A manifest can add only its own state directories, secrets, limits within infra's caps, and the one fixed relaxation. From the profile, infra renders the account (`<platform>-<service>` or `<platform>-<job>`), its groups, database role, Incus certificate, the unit `<platform>-<service>.service` (jobs: a container, §7.18), sockets and ACLs. `contract/profiles.yml` is the single source of this table and of its generated reference page.

| Profile | Database | Incus | Telemetry | Inference and GPU | Restricted data | Filesystem | Network |
|---|---|---|---|---|---|---|---|
| `controller` | own login role; table grants from migrations, `restricted` schema allowed | restricted certificate for projects `bench` and `build`; run networks only through the run-network helper (§7.7) | send; query Prometheus, Loki, Tempo through the query proxy only | inference API; the only service that may request GPU arbiter transitions (serve, train, idle, publish a dataset); never GPU devices | read | release (read); own config, secrets and state; signing key (0400); attestations and trusted keys, session records, fingerprint, models, adapter lineage (read) | loopback only, also with remote inference (§12.8); no listening TCP port |
| `inference-broker` | own login role; never `restricted` | none | send | inference API | never | release; own config, secrets, state | loopback only, also with remote inference (§12.8); may listen on exactly one port, the researcher endpoint `127.0.0.1:18705` |
| `sandbox-operator` | own login role; never `restricted` | restricted certificate for project `bench` | send; query (via proxy) | none | never | release; own config, secrets, state | loopback only, no listening port |
| `restricted-operator` | own login role; `restricted` allowed | restricted certificate for project `bench` | send | none | read | release; own config, secrets, state | loopback only, no listening port |
| `restricted-reader` | own login role; `restricted` allowed | none | send; query through the query proxy only | none | read | release; own config, secrets, state; optional tools | loopback only, no listening port |
| `observer` | own login role; never `restricted` | none | send; query through the query proxy only | none | never | release; own config, secrets, state | loopback only, no listening port |
| `untrusted-agent` | none: no role, database socket directories hidden | none | send only, through infra's separate agent OTLP socket; no query | none directly; only through a dedicated broker socket the manifest declares | never | allowlist: release (read), own state, own non-secret config, its dedicated sockets; the rest of `/srv/pinka`, `/etc/pinka`, `/var/lib/pinka`, `/run/pinka` hidden | none (`PrivateNetwork=yes`, Unix sockets only) |
| `gpu-trainer` (jobs only) | none | none | none; progress goes to the job log and a metrics file in its output directory | the GPU through CDI, only while the arbiter is in training mode for this job | never | mounts only: base model (read-only), declared datasets (read-only), its output and checkpoint directory (read-write); read-only root file system | none (`--network none`) |

"Never" means infra refuses the manifest and also revokes the grant at run time; "none" means the profile has no such access to begin with. Only `gpu-trainer` ever gets GPU devices; vLLM, which infra runs, is the only other GPU user, and the arbiter keeps the two apart. Every service gets the hardening baseline of §7.14, which a manifest cannot override; `untrusted-agent` gets the additions listed there, and `gpu-trainer` containers get the fixed options of §7.18. `observer` suits a dashboard or report exporter; the test fixtures cover it.

"Inference API" means vLLM's API, or on a lab node infra's inference forwarder (§12.8); no other profile reaches it (§7.11). "Query" means through infra's read-only query proxy (§7.9) only: the accounts of the profiles that query are the members of `pinka-query`. No profile reaches Prometheus, Loki or Tempo directly, and none can push to, delete from or administer them.

### 12.3 Sockets, principals and endpoints

A platform's services talk over Unix sockets that infra creates and permissions from the manifest; the only TCP listener a platform may have is the inference broker's researcher endpoint `127.0.0.1:18705`. Infra refuses any manifest that breaks the connection rules below.

**Rendering.** Each socket is `/run/pinka/platform/<service>/<socket>.sock`, owned by the serving account, mode 0660. Its group is a generated client group `<platform>-<service>-<socket>` holding the client service accounts (name lengths are capped so it fits 32 characters). Researchers and admins get POSIX ACL entries for the `pinka` and `pinka-admin` groups; without ACL support on `/run`, the platform's peer-credential check alone separates them. Root can always connect, for the hooks.

**Principals file.** Infra writes `/etc/pinka/platform/principals.json` (0644): every service and job with its account name and UID, every socket with its path and allowed clients, and the names of the researcher and admin groups. The platform builds its per-endpoint peer allowlists from this file instead of hard-coding names.

**Endpoints file.** Infra writes `/etc/pinka/platform/endpoints.toml` (0644): the addresses and paths infra provides to the platform, with the effective port values (§12.11). These are the lab facts (machine name, `lab_mode`, `node_role`); the PostgreSQL socket directories with the database and role names; the OTLP endpoint `127.0.0.1:4317/4318`, the VM collector address and the agent OTLP socket; the telemetry query proxy's socket (§7.9); the inference API (`inference.mode`: `local`, `remote` or `scripted`; with `remote`, the forwarder's loopback addresses); the OPA decision endpoint; the Incus HTTPS address, certificate directories, projects and the target CPU set (`cpuset_targets`, when it is set); the GPU arbiter socket; the run-network helper socket; the signing-key path; the attestation directories; the session records and the fingerprint command. The platform reads names and addresses from this file instead of hard-coding them.

**Who may connect to a socket served by which profile:**

| Served by | May be used by | Never by |
|---|---|---|
| `controller` | every service profile; researchers, admins, root; `untrusted-agent` only on a dedicated socket | — |
| `inference-broker` | `controller`, researchers, root; `untrusted-agent` only on a dedicated socket | `restricted-*` (no reason to) |
| `sandbox-operator` | `controller`, root; `untrusted-agent` only on a dedicated socket | researchers, `restricted-*` |
| `restricted-operator` | `controller`, root | everyone else, above all `untrusted-agent` |
| `restricted-reader` | `controller`, root | everyone else, above all `untrusted-agent` |
| `observer` | every service profile except `untrusted-agent`; researchers, admins, root | `untrusted-agent` |
| `untrusted-agent` | serves no sockets | — |
| `gpu-trainer` | serves and uses no sockets: a job has no network and no socket mounts | — |

**Dedicated agent sockets.** Every socket the agent may use is dedicated to it. A socket marked `untrusted_clients: true` lists `untrusted-agent` as its only client, whichever profile serves it (`controller`, `inference-broker` or `sandbox-operator`). The validator refuses the flag together with any other client, profile or human, and refuses `untrusted-agent` on any socket without the flag; a service that serves the agent and other clients therefore has separate sockets for them. The platform's specification obliges that no endpoint on such a socket returns restricted data, and a platform test proves it. The installer's review screen and doctor highlight every such socket.

Doctor checks each socket's owner, mode and ACL, and also lists the sockets the running agent can actually see inside its own mount namespace; anything beyond its allowlist fails the check.

### 12.4 Restricted data

Ground truth is one case of a generic class, restricted data, that infra provides to every platform: a filesystem area, a group and a database schema. Infra enforces that `untrusted-agent` and `gpu-trainer` never reach any of them, whatever the manifest says.

| Part | Value |
|---|---|
| Area | `/srv/pinka/restricted/<platform>/`, root:`pinka-restricted` 0750, files 0640; restricted content packages under `content/<package>/<ref>/` |
| Group | `pinka-restricted`, an infra group; its members are exactly the accounts of `controller`, `restricted-operator` and `restricted-reader` services |
| Database schema | `restricted` in the platform's database, owned by the migrate role |
| Content | packages the manifest declares `restricted: true` (the Pokus Platform: its scenario catalog) |

**Enforced regardless of the manifest:**

- An `untrusted-agent` or `gpu-trainer` account is never a member of `pinka-restricted`; the users-and-groups step refuses to render such a membership even if data asked for it.
- The restricted area, every restricted content directory and the database socket directories are outside the agent's filesystem allowlist and are never mounted into a training container; neither profile has a database role.
- No socket served by a `restricted-*` profile lists anyone but the controller and root as clients.
- After every migration, infra revokes all privileges on schema `restricted` from every role outside `controller`, `restricted-operator` and `restricted-reader`, and from the researchers' reader and Grafana roles. A grant that a migration made to a forbidden role fails the deployment, and the release does not start (§7.8).
- Restricted content is extracted only into the restricted area, never into the release directory, the platform state or anywhere else; doctor fails if a restricted package's provenance record appears elsewhere.
- Backups of the restricted area and of the database are written root-only.
- Data derived from restricted data reaches a training job only as a published dataset that passed the split guard (§7.18).

**What the platform must guarantee.** Endpoints on a dedicated agent socket never return restricted data, and a platform test proves it; infra makes these sockets visible but cannot inspect what they return. The dataset provenance the platform writes is complete and truthful; infra checks it but cannot verify it against the restricted data.

### 12.5 Database

| Item | Value |
|---|---|
| Database | one per platform, named after it (the Pokus Platform's: `potka`); Unix sockets only, no TCP listener (§7.8) |
| Login roles | `<platform>_<service>` for every service except `untrusted-agent` |
| Group roles | `<platform>_<profile>` (for example `potka_controller`), so migrations can grant per profile |
| Owner of the schemas | `<platform>_migrator`, used only by the `migrate` hook |
| Schemas | `public`, `restricted` |
| Restricted schema | revoked after every migration from every role outside the three restricted-capable profiles, the reader and Grafana; a violating grant fails the deployment |
| Reader and Grafana roles | `<platform>_reader` (researchers), `<platform>_grafana`; never `restricted` |
| Service socket directory | `/run/pinka/postgres/`, group `<platform>-db`, containing only services that have a role; the agent is never a member |
| Reader socket directory | `/run/pinka/postgres-reader/`, group `pinka`: the researchers' reader role and Grafana, which mounts the directory; the reader's password file is 0640 root:`pinka` |
| Password files | `/etc/pinka/secrets/postgres/<role>`; the migrator's readable only by `<platform>-migrate` |
| Infra's own role | `pinka_monitor` |

### 12.6 Incus and targets

| Item | Value |
|---|---|
| Credentials | one restricted client certificate per service whose profile has Incus access, `/etc/pinka/incus/<platform>-<service>/{client.crt,client.key,server.crt}` (0600, owned by its account): `controller` for `bench` and `build`, `sandbox-operator` and `restricted-operator` for `bench`; the Incus HTTPS listener on `127.0.0.1` |
| Projects | `bench` (official runs), `build` (image builds), personal per-user projects |
| Network conventions | per-run networks named `r-<short>` (12 lowercase letters and digits), created and deleted by the controller only through the run-network helper `/run/pinka/run-network.sock` (§7.7), with fixed settings (no NAT, no uplink, ACL `pinka-run-default`); bridge prefix for firewall compatibility |
| Profiles | `target-container`, `target-vm` (with `vm_targets`), `probe-client` |
| CPU sets | with `cpuset_targets` in host_vars, `endpoints.toml` carries `[incus] cpuset_targets` (a Linux cpuset list). The platform sets every target instance's `limits.cpu` to a CPU range inside it, never a plain count (a count lets Incus place the instance on any host CPU); infra sets the `bench` project's `limits.cpu` to the number of those CPUs and runs its own services on `cpuset_services`. A platform refuses measurement campaigns on a host without `cpuset_targets`. The installer and doctor warn when SMT siblings of one core fall into different sets |
| Telemetry entry for targets | the platform attaches an Incus proxy device to each target instance: `listen=tcp:127.0.0.1:4317` (inside instance) → `connect=tcp:127.0.0.1:4317` (host collector), `bind=instance`. VMs cannot use such a proxy device (Incus supports proxy devices on VMs only in NAT mode, which needs `bind=host`), so they send OTLP to the collector listener on `vm_collector_address` (default `198.18.0.1`, on the dummy interface `pinka-otlp0`, §12.11); the run ACL `pinka-run-default` allows egress to exactly that address and the OTLP ports, and the host firewall accepts nothing else from run networks |
| Telemetry identity | the platform sets `OTEL_RESOURCE_ATTRIBUTES` with its own run attributes in each target instance's environment at launch (the Pokus Platform: `potka.run_id=<ULID>,potka.node=<alias>`); the in-image OTel agent must honor this variable. Run identifiers are opaque and carry no scenario information. Target profiles set the Incus guest API off (`security.guestapi=false`, verify the key name) so instance config cannot be read from inside. No scenario identifiers ever enter the instance. |
| Events | `pinka-incus-events.service` ships Incus lifecycle and logging events to Loki for the platform's integrity checks |
| Golden images | aliases `<blueprint>/<version>` in Incus with properties `pinka.recipe_commit`, `pinka.built_at`, `pinka.base_fingerprint` and Incus's `requirements.nesting`; the platform launches an image with `security.nesting=true` exactly when it carries `requirements.nesting=true` |

A restricted Incus certificate has full access inside its projects, so a sandbox operator can do in `bench` whatever the controller can; narrowing that needs Incus's fine-grained authorization, which is out of scope.

### 12.7 Telemetry

| Item | Value |
|---|---|
| OTLP for services | `127.0.0.1:4317` (gRPC) and `127.0.0.1:4318` (HTTP), for every profile that sends, except `untrusted-agent`; VM targets use `vm_collector_address` (§12.6) |
| Agent OTLP | `/run/pinka/otlp/agent.sock` only; everything received there is stamped `pinka.trust=untrusted` (§7.9) |
| Query APIs | Prometheus, Loki and Tempo through the read-only query proxy `/run/pinka/query.sock` only (§7.9), for the profiles that query (`controller`, `sandbox-operator`, `restricted-reader`, `observer`): query endpoints only; push, remote write, delete, admin and configuration requests are refused; Prometheus, Loki and Tempo have no listener a profile can reach |
| Loki labels | at most four resource attributes from the manifest's `telemetry.labels` |
| Dashboards | the manifest's `dashboards`, provisioned with a read-only datasource on `<platform>_grafana` through the reader socket directory, and with the telemetry datasources through the query proxy |

### 12.8 Inference and the GPU

| Item | Value |
|---|---|
| Inference API | vLLM's OpenAI-compatible API on `127.0.0.1` (`inference.mode = local`), or, with `remote` (`node_role: lab`, §19.10), infra's inference forwarder `pinka-inference-forwarder` on the lab node's loopback: a root-run forwarder that alone holds the mutual-TLS client certificate (`/etc/pinka/inference/`, root only) and dials the paired inference node over the tailnet; it offers the API on the same loopback port as vLLM on a full node and the control API of §19.10 on `127.0.0.1:18710`, so platform services handle no TLS and no keys and stay loopback-only; `scripted`: any OpenAI-compatible endpoint configured in `endpoints.toml` (no scripted model server is built here), only on `lab_mode: staging`, and every run using it is `official=false` |
| Deployments | deployment specs `/etc/pinka/vllm/deployments/<id>@<version>.yml` with the fields `id` and `version` matching the file name (§7.11); switched only through the GPU arbiter's `serve <id>@<version>` request, never by restarting units |
| Remote control API | the inference node's control endpoint, reached through the forwarder: exactly `GET /health`, `GET /model`, `GET /metrics`, `POST /activate` and `POST /verify` (§19.10); `POST /activate` reaches the inference node's GPU arbiter as `serve <id>@<version>` |
| Adapters | `adapter_ids` name registered adapters as `<name>@<sha256>` |
| GPU arbiter | `/run/pinka/gpu.sock`, for the controller only: requests `serve`, `train`, `idle`, `status`, `publish-dataset` (§7.18) |
| Training records | `PROVENANCE.json` per published dataset; `MANIFEST.json` and `LINEAGE.json` per registered adapter; schemas in `contract/records/` |

### 12.9 Hooks

Infra drives the platform only through four hooks and the paths in its manifest; `pinka`, doctor, the health gate and backups name no platform service, socket or endpoint. A hook is a command inside the release. Infra runs it as a transient systemd unit (`pinka-hook@`) under a fixed account with that profile's hardening, in the release directory, with a timeout (SIGTERM, then SIGKILL). The hook prints one JSON line on stdout; everything else goes to the journal. Exit code 0 with `"ok": true` is success.

| Hook | When infra runs it | Account | Output | On failure |
|---|---|---|---|---|
| `migrate` | at deployment, after the build, before the new release starts | `<platform>-migrate` (only the migrator password, the database socket) | `{"ok": true}` | deployment stops; the platform stays stopped; the restricted-schema check runs after success |
| `reconcile` | at every start, before the services (a oneshot unit ordered before them) | the controller's account | `{"ok": true}` | services do not start; doctor reports it |
| `drain` | `pinka stop`, `pinka update-host`, deployment of a new release, restore; argument `--timeout <seconds>` | the controller's account | `{"drained": true, "active": 0}` | infra stops the services after the timeout; the platform records what was interrupted |
| `health` | after start until healthy or timeout; `pinka status`; doctor | the controller's account | `{"ok": true, "detail": {…}}` | start reports "unhealthy"; doctor warns |

### 12.10 Provenance, records and keys

| Item | Value |
|---|---|
| Infra provenance | `/etc/pinka/bootstrap-state.json`: repository commit, tree, ref (the release tag) and dirty flag |
| Platform provenance | `DEPLOYED_FROM.json` in the release and in each content package: source, commit, tree, tag, platform name, version, runtime and the manifest's SHA-256 |
| Active platform | `/etc/pinka/platform/active.json` |
| Session records | `/var/lib/pinka/sessions/<boot_id>.json`, read by the controller; format of §7.16, JSON Schema in `contract/records/` |
| Fingerprint | `pinka fingerprint --json`, callable by the controller, prints the document of §10.2 (JSON Schema in `contract/records/`); run by any account but root it prints the cached root-computed fingerprint with its `computed_at` time and never escalates |
| Signing key | one per platform, `/etc/pinka/signing/<platform>/` (ed25519, PEM PKCS#8), 0400 for its controller, generated on the host; its public key (PEM SubjectPublicKeyInfo) is published as `/srv/pinka/shared/signing-keys/<platform>-<machine>-<YYYYMMDD>.pub` and kept forever; key ID: the lowercase hex SHA-256 of the DER-encoded public key |
| Attestations | `/etc/pinka/qa/trusted-keys/` and `/etc/pinka/qa/attestations/`, readable by the active platform's controller; the attestation format and the official check are the platform's |
| `lab_mode` | `production` or `staging` (§13, invariant 10); `scripted` inference only on `staging`; staging on production's hardware only inside a maintenance window (§7.20) |

For the Pokus Platform, a release candidate is an infra release, a platform release and its content packages: with the catalog as a content package, the same triple as with contract v1.

### 12.11 Ports

Every TCP listener of the lab binds to `127.0.0.1` unless the table says otherwise or it is in the non-loopback block below. `docs/contracts.md` carries the same table with its defaults. Each port can be overridden in host_vars (`ports:`); infra writes the effective values into `endpoints.toml`, and the platform reads them from there, never from the defaults. Preflight reports a configured port that a process outside the lab already uses (§2.2). Exporters (the GPU exporter included, nvidia-smi or DCGM, §7.9) and internal health endpoints have no contract port and bind to `127.0.0.1` or an internal network; PostgreSQL has no TCP listener at all.

| Default port | Listener | Bound to |
|---|---|---|
| 4317, 4318 | OpenTelemetry Collector, OTLP gRPC and HTTP | `127.0.0.1` |
| 8181 | OPA: decision path and health only (§7.10) | `127.0.0.1` |
| 8443 | Incus HTTPS API: restricted TLS clients, metrics | `127.0.0.1` |
| 18700 | Grafana | `127.0.0.1`; optionally `tailscale serve` |
| 18701, 18702, 18703 | Prometheus, Loki and Tempo | reachable directly only by the collector and the query proxy (§7.9) |
| 18704 | vLLM OpenAI-compatible API | `127.0.0.1` |
| 18705 | the inference broker's researcher endpoint | `127.0.0.1` |
| 18706 | docs service (§7.19) | `127.0.0.1`; optionally `tailscale serve` |
| 18704, 18710 (lab node) | inference forwarder: the remote inference API and control API (§12.8) | `127.0.0.1` |

**Non-loopback block.** Spec features need these listeners. Each is bound to one specific address and firewalled; none is public.

| Default port | Listener | Bound to |
|---|---|---|
| 18704, 18710 | inference control endpoint (`node_role: inference`): mutual TLS; 18704 forwards the vLLM API from `127.0.0.1:18704`, 18710 is the control API; pairing uses 18710 while the endpoint is stopped (§19.10) | the inference node's tailnet address |
| 4317 | OTLP gRPC for the paired inference node's telemetry, mutual TLS (`node_role: lab`) | the lab node's tailnet address |
| 4317, 4318 | collector listener for VM targets (`vm_targets`), reachable from run networks only (§12.6) | `vm_collector_address` (default `198.18.0.1`) on the dummy interface `pinka-otlp0` |

---

## 13. Safety invariants (non-negotiable; put them in `CLAUDE.md`)

1. Nothing is ever formatted, wiped, or repartitioned unless `incus_pool_source: device` **and** `incus_pool_device_confirm` equals `incus_pool_device` exactly **and** the device is not mounted, not part of any existing filesystem/LVM/RAID signature (check with `lsblk -f`/`wipefs -n`) unless `incus_pool_device_force_wipe: true` is also set. Print the device's model/size/serial and require interactive confirmation unless `--non-interactive` with all vars set.
2. Never install a Linux NVIDIA kernel driver on WSL. Never modify anything on the Windows side. Nothing in the core may call Windows executables.
3. Never enable Tailscale Funnel. Never bind lab services to non-loopback interfaces, except sshd (firewalled) and the non-loopback block of §12.11.
4. Humans are never added to `docker` or `incus-admin` implicitly. An `untrusted-agent` service gets no network, database, Incus, Docker, restricted data or secrets, no relaxation, and no group except the client groups of its dedicated sockets (§12.2).
5. Secrets never committed in plaintext, never logged (`no_log`), never baked into images.
6. Every external artifact pinned and verified (§10). No `latest` tags. No `curl | bash`.
7. No automatic reboots without `--allow-reboot`. No automatic kernel or driver upgrades.
8. Idempotent: second run reports `changed=0`.
9. Do not flush or rewrite firewall tables owned by Docker, Incus, LXD, Podman, firewalld or ufw.
10. Hosts with `lab_mode: staging` use their own vault, `/srv/pinka`, Tailscale identity (QA tag) and signing key, and are never paired with or allowed to write to a production host. Infra installs QA trusted public keys into `/etc/pinka/qa/trusted-keys/` from `qa_trusted_keys` in group_vars, and provides `/etc/pinka/qa/attestations/` (root-owned, readable by the active platform's controller) where a human places published QA attestations. On shared hardware (`staging_shared_hardware: true`), staging runs only inside an open maintenance window with the GPU idle and its ports free, and production stays stopped while its own window record exists (§7.20).
11. Every override (`--force-unsupported`, `--accept-degraded`, `--accept-experimental`, `--allow-dirty`, break-glass groups) is recorded in the host profile and environment fingerprint.
12. **No personal or installation-specific data in the repository.** No real names, usernames, e-mail addresses, organization or institution names, hostnames, domains, IP or MAC addresses, hardware serials/UUIDs, SSH keys, Tailscale identities, or anything else that identifies a person or a specific installation — in code, configuration, examples, fixtures, tests, documentation, screenshots or generated files. Use neutral placeholders: `researcher1`, `researcher2`, …; `example-org`; `example.com` / `example.org` / `example.net` (RFC 2606); documentation IP ranges `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32` (private RFC 1918 and CGNAT ranges are allowed only as technical defaults such as address pools); MACs from `00:00:5e:00:53:00/24` (RFC 7042); zeroed serials and UUIDs. Real deployment data (host_vars of real machines, `users/*.pub`, installer answers, vaults) belongs only in the deploying lab's own private fork or configuration repository: `.gitignore` excludes it from the public project, the installer's commit offer warns when the target remote is the public upstream, and fixtures captured from real hosts pass a documented sanitizer first. A CI **privacy scan** enforces this (e-mail addresses outside RFC 2606 domains, public IP addresses, MAC addresses outside the documentation range, SSH/PGP key material outside clearly marked test fixtures, and an optional maintainer-supplied denylist provided as a CI secret, never committed). This rule is about data tied to a **person or a specific installation**; it does **not** restrict references to public services, vendors and projects (GitHub, GitHub Actions, Docker Hub, Hugging Face, Tailscale, distribution mirrors, upstream documentation, model names) or to the Pokus project's own public repository and organization once it is published. Pokus is an open-source project meant to be deployed and customized by anyone, so every person- or installation-specific value is supplied by the deployer at deploy time and never baked into the project.
13. Platform privileges come only from the eight profiles (§12.2): no manifest field adds a group, capability, device, path, mount or address, and every refusal happens before anything on the host changes.
14. Every name a manifest would create is checked against the reserved-name lists and against the host (§12.1).
15. The restricted area, group and schema are closed to every profile except `controller`, `restricted-operator` and `restricted-reader`; `untrusted-agent` and `gpu-trainer` never reach them, and the schema is closed again after every migration (§12.4).
16. Every socket the agent may use is dedicated to it (`untrusted_clients: true`); sockets of `restricted-*` services accept only the controller and root (§12.3).
17. Only `gpu-trainer` containers ever get GPU devices, and only while the GPU arbiter is in training mode for that job; the GPU is in exactly one mode at a time (§7.18).
18. Training containers run with infra's fixed options (§7.18), never with options from a manifest.
19. No dataset whose provenance lists a `test`-split instance is published or mounted (the split guard).
20. Adapters are registered read-only under their hash with a lineage record; vLLM loads only registered adapters.
21. Platform services query telemetry only through the read-only query proxy (§7.9); none reaches Prometheus, Loki or Tempo directly, to query, push, delete or administer. The reference platform's isolation probe proves it, and doctor checks it on every host.
22. Only the inference broker, the controller and infra's own checks reach the inference API (vLLM, or the inference forwarder on a lab node); platform services never hold the inference node's keys (§7.11, §12.8).
23. Run networks are created and deleted only through the run-network helper, with its fixed settings; every operation is recorded (§7.7).
24. Pre-existing components (Docker Engine, Tailscale, LXD, Podman, an Incus not adopted with `--adopt-incus`, an NVIDIA driver, user accounts) are never changed, stopped, restarted or reconfigured (a Tailscale that is installed but not logged in is not in use, §7.4); where the lab cannot run without such a change, preflight stops before anything changes and names the change for the owner to make (§2.2, §7.21).
25. Uninstall removes only what the install record names as pinka's (on a host without a record, only what carries pinka's names), never a pre-existing component, and keeps the lab's data unless `--purge-data` is given with the typed machine name (§5.4).

## 14. Code style and conventions

- Bash: `set -Eeuo pipefail`, functions with `local`, quoted expansions, `readonly` constants, a single logging helper (`log_info`, `log_warn`, `log_error`) with UTC timestamps.
- Python: type hints everywhere, dataclasses for report structures, no global state, pure detection functions taking `(fs, runner)`; `ruff` + `mypy --strict` clean.
- Ansible: FQCN modules, `name:` on every task, handlers for restarts, `block/rescue` for multi-step operations, no `ignore_errors: true` without justification comment, variables prefixed by role name in `defaults` (`incus_pool_size`, `docker_data_root`, ...).
- Templates carry a header: `# Managed by pinka (<template path>). Local changes will be overwritten.`
- All timestamps UTC ISO-8601 with `Z`.
- Documentation in English; clear, concise, with copy-pasteable commands.

## 15. Documentation deliverables

Follow `docs/DOCS_SPEC.md`. The list below is the infra-specific minimum; the spec's required pages (operator tutorials and how-tos, troubleshooting by exit code and doctor check, generated references for CLI, Ansible variables via `meta/argument_specs.yml`, schemas, support matrix, versions, filesystem/accounts/sockets), page conventions, quality gates (strict build, docs coverage, drift check) and definition of done all apply.

- `README.md`: what this is, supported environments table (generated excerpt), prerequisites, quickstart (`git clone`, create host_vars, create vault, `sudo ./bootstrap/bootstrap.sh --machine <name>`), exit codes table, where to look when something fails.
- `docs/architecture.md`: trust zones (Windows/host OS boundary when relevant, lab host, control plane, benchmark sandbox, personal sandboxes), component diagram (Mermaid), data flows (telemetry path via proxy devices, executor path via Incus API), generated layout table.
- `docs/operations.md`: daily start/stop, adding/removing a researcher, rotating secrets and certs (`rotate.yml`), building and exporting golden images, adding a model, switching vLLM deployments manually (for debugging), backup/restore, **migrating to a new machine** (fresh build + restore; tailnet node replacement; signing key handover), troubleshooting by exit code.
- `docs/security.md`: threat model (misbehaving agent, buggy fault injection, compromised target, a compromised platform service tampering with telemetry, researcher mistakes), mitigations mapped to mechanisms, Tailscale ACL snippet, what is explicitly out of scope.
- `docs/reproducibility.md`: pinning policy, fingerprint, what makes two runs comparable, lock update procedure.
- `docs/providers/wsl2.md`: WSL specifics, `.wslconfig` recommendations (memory, processors, swap, `nestedVirtualization`, `localhostForwarding=false`), the optional `contrib/windows` keep-alive helper, `wsl --terminate` restart flow, known limitations (no DCGM, nested KVM caveats, Windows build not observable).
- `docs/providers/baremetal.md`: NVIDIA driver + Secure Boot/MOK, dedicated pool disk, power policies.
- `docs/providers/vm.md`: GPU passthrough requirements per hypervisor, nested virtualization, measurement caveats.
- ADRs: Incus for targets (vs Docker/VMs only), native Docker Engine (vs Docker Desktop), provider model, family-based portability with a data-driven component source matrix, uv-managed Python instead of distro Python, firewall backend integration instead of replacement, stdlib-only preflight, Postgres socket-only access, OTel-based telemetry, btrfs/LVM-thin requirement (no `dir` backend), no orchestration frameworks.
- Contract v2: `docs/contracts.md` rewritten as contract v2 (profiles, manifest, reserved names, paths, sockets, principals and endpoints files, database, Incus, telemetry, inference, the GPU arbiter and jobs, hooks, provenance and lineage); drift-checked generated references for the manifest (from the schema's descriptions), the profile grants (from `contract/profiles.yml`), the reserved names, the principals and endpoints files and the provenance and lineage records; how-tos "Plug a platform into the lab", "Replace or purge a platform" and "Run a training job and serve its adapter"; the tutorial "Write a minimal platform" (the reference platform); explanations "Privilege profiles and restricted data" and "The GPU arbiter" (split-node training recorded as future work); updated `security.md`, `operations.md`, backup and restore guides, `docs/providers/wsl2.md` (CDI for training containers) and exit-code reference (65); ADR-0014 "Platforms plug in through manifests and fixed privilege profiles" and ADR-0015 "GPU training as infra-run container jobs".
- Plan item 3f-1: the how-tos "Uninstall the lab" and "Install next to existing Docker, Tailscale, LXD or Podman"; the explanations "Pre-existing components" (what pinka uses as it is, what it refuses, the trade-off of an adopted Tailscale node) and "Capacity planning" (the requirements computation, estimated versus measured numbers, growth); troubleshooting sections for exit codes 80 and 81; the support matrix and `docs/distros/debian.md` for Ubuntu 26.04 (Incus from universe).

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
10. `pinka` CLI with doctor (incl. `--smoke`) and fingerprint.
11. `catalog` and `platform` roles against contract v1 (with `*_enabled: false` defaults); contract v2 replaces them in steps 15–22.
12. `make test-distros` harness (§19.9) and the generated support matrix with test dates.
13. Installer TUI (§18): screens, validators, answers file, resume state, Ansible event callback, tests.
14. Documentation and ADRs; regenerate generated docs; final `make lint test lock-check`.

Contract v2 steps, after step 14:

15. Contract v2 core: manifest schema with the reserved lists, the eight profiles, validator and invariant checks, principals and endpoints files.
16. Deployment: sources and content packages, staging, sandboxed build, job image pulls by digest, provenance, the one-platform rule, replace and purge.
17. Rendering from profiles: accounts, groups, units, sockets (dedicated agent sockets included), ACLs, database and `restricted` schema, Incus certificates, the run-network helper, OPA, dashboards, tools, CLI link; platform special cases removed; the neutrality lint; tests proving that the agent has no IP access and no database socket group.
18. Operations: hooks in `pinka` start, stop, status and update-host; the health gate's `requires`; backup and restore; doctor checks; the agent OTLP socket and the telemetry query proxy; port overrides and the preflight port and VM-address checks (§12.11); the driver CUDA check (§2.2); the session record and fingerprint schemas in `contract/records/`; the docs service (§7.19) and maintenance windows (§7.20).
19. GPU arbiter and training jobs: the arbiter replacing the deploy helper (modes, transitions, boot handling, `pinka gpu`); the inference forwarder and the control API with `POST /activate` through the arbiter; the inference API's reachability (§7.11); training containers; datasets area and split guard; adapter registration, lineage, `adapter_ids`.
20. Reference platform: `refplat` with its training job and CPU-only variant, the isolation probe (per loopback-capable profile), the extended check-mode job, the `make test-distros` additions.
21. Installer: release inspection, the Platform review screen, the GPU screen, sources, secrets, answers schema, snapshots.
22. Documentation: contract v2, generated references, new pages, ADR-0014 and ADR-0015, security.

After each numbered step: write or update the documentation for what the step built (per `docs/DOCS_SPEC.md`), run `make lint test docs`, commit, and give me a short summary of what was built, what was verified, and what remains `TODO(verify)`.

## 17. Definition of done

- `make lint test lock-check` passes with zero `TODO(verify)` items (or each remaining one explicitly approved by me and listed).
- All preflight fixtures produce the expected outcome, provider, and features.
- `sudo ./bootstrap/bootstrap.sh --machine example-machine --check` runs end-to-end in check mode on an Ubuntu 24.04 test host without errors.
- `make test-distros` (§19.9) passes on every `supported` and `tested` distribution: full bootstrap without GPU, a second run with `changed=0`, and `pinka doctor --smoke` exit 0.
- The `make test-distros` uninstall lane passes on every `supported` and `tested` distribution, and so does its pre-existing variant (§19.9): after uninstall the host's snapshot equals the one before install, apart from the items listed as kept on purpose.
- Preflight, the installer and doctor show the same requirements for the same choices (§2.3).
- On a real supported host (run by me): a full bootstrap completes, a second run reports `changed=0`, and `pinka doctor --smoke` exits 0.
- Every role has a README and `meta/argument_specs.yml`; every variable is documented; generated docs are in sync.
- `make docs` builds in strict mode with zero warnings; the docs coverage check reports 100 %; every infra page required by `docs/DOCS_SPEC.md` §A.3 exists, and every tutorial and how-to has `qa:run` markers on its executable blocks.
- `CLAUDE.md` captures the invariants so future sessions keep them.
- The reference platform deploys and its isolation probe passes in `make test-distros`; the neutrality lint passes; the Pokus Platform's manifest fixture validates; the training flow (dataset, job, adapter, serving) runs end to end, on the GPU or as the CPU-only variant.
- The TUI completes every flow in §18.9 in pilot tests; snapshot tests pass at 80×24 and 120×40; an install done through the TUI and a replay with `--answers` + `--non-interactive` produce byte-identical config files (excluding the encrypted vault) and the same Ansible result.
- On a real host: a fresh install through the TUI ends with vLLM serving the selected model and passing the tool-call verification.

---

## 18. Installer TUI (`installer/`)

### 18.1 Goals and principles

- A polished, keyboard-first terminal UI that works over SSH and inside WSL terminals, with an 80×24 minimum size, optional mouse support, `NO_COLOR` support, and a high-contrast theme.
- **Every input the lab needs is collected here.** Nothing later prompts on raw stdin: Ansible prompts are disabled, and things like the Tailscale login URL are shown inside the TUI.
- **Front-end only.** The TUI writes configuration files and invokes `pinka-preflight`, `ansible-playbook` (via bootstrap's own functions) and `pinka doctor` exactly as a non-interactive run would.
- **Resumable.** Progress is persisted, so the install survives `wsl --terminate`, reboots, SSH disconnects and crashes, and continues at the right step when bootstrap is started again.
- **Safe.** Secrets are masked and never written to answers files or logs; destructive choices require typed confirmation; nothing is applied before the Review screen.
- **Re-runnable.** On an already installed host, the TUI opens in *modify* mode, pre-filled from the existing host_vars and answers file, and shows a diff of every file before applying.

### 18.2 Technology

- Python 3.12, **Textual** (pinned), **ruamel.yaml** (round-trip, preserves comments in host_vars) in a hash-locked uv environment under `/opt/pinka/installer-venv` (it may share a lockfile with the Ansible environment; decide and document).
- Launched by `bootstrap.sh` under sudo. Every file written into the repo checkout is chowned to `SUDO_UID:SUDO_GID`, so the repository never becomes root-owned.
- If the terminal is unsuitable (`TERM=dumb`, no TTY, too small), refuse to start the TUI and print how to use `--answers` mode instead.

### 18.3 Files the TUI writes

| File | Content | Committed to git? |
|---|---|---|
| `ansible/inventory/host_vars/<machine>.yml` | all non-secret machine settings, including the `platform` and `content` source blocks, `platform_settings`, `gpu_arbiter` and `training_caps` (§8.1) | yes |
| `ansible/group_vars/all/main.yml` (researchers section only, round-trip edit) | lab-wide researcher list | yes |
| `users/<name>.pub` | researchers' SSH public keys | yes |
| `ansible/group_vars/all/vault.yml` | all secrets, Ansible Vault encrypted | yes (encrypted only) |
| `models.lock.yml` | selected models with pinned revisions and hashes | yes |
| `installer-answers/<machine>.yml` | every non-secret choice, for `--answers` replay | yes |
| `/var/lib/pinka/installer/state.json` | resume state: current screen, completed phases, timestamps (0600 root) | no |

At the end, offer to create a git commit of these files on branch `machine/<name>` with the invoking user as author. Never push automatically; remind the operator to push.

### 18.4 Screen flow

Every screen has Back / Next, inline validation messages next to the field, contextual help (F1), and a progress indicator ("step 5/17 · Capacity plan"). Sections not applicable to the host (e.g. NVIDIA driver on WSL) are shown greyed out with a one-line explanation, not hidden silently.

1. **Welcome** — repo commit, ref, dirty flag, log file path, keyboard help. If `state.json` exists: "Resume previous install (step X, started at …)" or "Start over".
2. **Preflight** — runs the detector with a live spinner, then shows cards: Environment (class, hypervisor, confidence, expandable signals), OS & kernel, CPU/RAM/disk, GPU, Virtualization (KVM, nested), Storage backends, Network endpoints, Conflicts. An outcome banner on top. Actions: Re-check, Raw JSON, Continue / Abort.
   - `degraded` → consent screen listing each disabled feature with its research impact (e.g. "No KVM: VM-class scenarios (kernel-level faults) cannot run on this host"); one checkbox per feature.
   - experimental tier → consent screen.
   - `needs_action` → step-by-step remediation and a Re-check button; for the Secure Boot rule with `install_nvidia_driver: auto`, a consent to MOK enrollment that turns it into a planned action (§3.2).
   - `unsupported` → Abort, or "Force (recorded in every fingerprint)", which requires typing `FORCE UNSUPPORTED`.
3. **Install mode** — Fresh install / Restore from backup / Modify existing / Uninstall (the last two offered only when an existing install is detected). Uninstall shows the dry run (§5.4), offers a backup when a restic repository is configured, asks for the typed machine name and, to remove the lab's data too, a second typed confirmation, then runs the same command as `bootstrap.sh --uninstall` and shows its report.
4. **Machine** — machine name (hostname rules, must be unique among host_vars), provider (read-only from preflight; override only if detection confidence is low), power policy (bare metal/VM only), `/srv/pinka` location (checks filesystem type and free space), Docker data root.
5. **Capacity plan** — one screen for everything that sizes the lab (it replaces the separate Resources, Storage and GPU & inference screens). It shows the detected machine and the choices: features, node role, the vLLM serving profile (auto-selected from GPU name and VRAM, overridable), the default model from `models.catalog.yml` filtered by profile fit (VRAM, license, gated, tool-call support) with its size, plus "custom (HF repo + 40-hex revision)" and "none", the storage backend from features (btrfs, or LVM-thin fallback) and its source (*loop file* with its size, or *dedicated disk*), the number of concurrent targets, the retention settings, the cpuset split between lab services and targets (a visual bar; ranges validated, no overlap; a warning when SMT siblings of one core fall into different sets, since target measurements would then share cores with the lab services) and Postgres memory. On WSL it explains that effective limits come from the Windows-side `.wslconfig` and captures the expected values. Budget bars for RAM, disk per mount point and VRAM, each expandable to its items, are green (fits), yellow (degraded, needs consent) or red (shows the shortfall and blocks Next); below them the expected growth per day and week and the date the disk fills at that rate. Every number is marked measured or estimated. The screen only renders preflight's `--plan` output (§2.3); a non-interactive install runs the same computation and writes the same breakdown to the log. Its sub-steps:
   - **Storage device** (dedicated disk only): a device table (name, by-id path, model, size, serial, existing signatures, mount points). Mounted or system disks are disabled. Selecting a disk opens a red danger dialog that requires typing the full by-id path; if signatures exist, an extra `WIPE` confirmation. Writes `incus_pool_device` and `incus_pool_device_confirm`.
   - **NVIDIA driver** (bare metal/VM with a GPU): install the NVIDIA driver? Branch selection (options from `versions.lock.yml`). If Secure Boot is on, explain the MOK flow and ask for consent to MOK enrollment (§3.2; the answers file records it as `gpu.accept_mok_enrollment`); with consent, ask for a one-time MOK password (masked, entered twice), passed to bootstrap and from there to `mokutil --import` via stdin only, never stored.
   - **Serving** (with a GPU): the Hugging Face token if a gated model is chosen (masked; validated with the HF `whoami` API when the network is available); start serving after install (default on); the GPU arbiter's mode after boot (serve the default deployment, or stay idle) and the caps for training jobs (timeout, memory, CPUs). On a `lab` or `inference` node the step states that training is unavailable there.
6. **Researchers** — table with add/edit/remove. Fields: username (Linux username rules, not reserved, no clash with service accounts), display name (Grafana), admin flag (at least one admin required), SSH public keys (paste multi-line or file path; validated with `ssh-keygen -lf`; private keys rejected explicitly; fingerprints shown), Grafana account toggle. Removed users go to `researchers_removed`. Warn if the invoking user is not in the list.
7. **Remote access** — Tailscale hostname (default: machine name), tag (default `tag:pinka-lab`), login method (masked auth key, or interactive login whose URL and QR code are shown during install), accept-DNS toggle (default off), Grafana and docs via `tailscale serve` toggles, the docs release to serve (tarball and sha256, optional, §7.19). Extra SSH interfaces: multi-select of detected NICs (disabled on WSL with an explanation). Show the required tailnet ACL snippet with a copy action. On an adopted Tailscale node (§7.4) the hostname, tag and login are shown as recorded and read-only, with the trade-off explained, and the serve toggles need an explicit consent checkbox (`tailscale_serve_consent`).
8. **Data & backups** — restic repository type: SFTP (host on the tailnet, user, path), REST server (URL), local path (warned: not a real backup), or "configure later" (doctor will warn). A *Test connection* action. restic password: generate (shown once; the operator must tick "I stored this safely") or enter. Schedule time, retention (daily/weekly/monthly), include model weights toggle, include secrets toggle (with warning).
9. **Restore** (restore mode only) — list snapshots from the configured repository (time, host, size), pick one, preview what will be restored.
10. **Platform** — no platform (standalone lab), or the platform source: repository URL (SSH, anonymous HTTPS or `file://`), ref (tag), path in the repository, tag prefix; authentication: generate a read-only deploy key (show the public key to add to the Git host), use an existing key path, or anonymous HTTPS for a public repository (no personal access tokens); the Git host's keys are pinned; require signed tags toggle; *Test access* action (`git ls-remote`). The installer then inspects the release (`bootstrap.sh --inspect-platform`) and asks for the source of every content package the manifest declares, one masked field per secret the manifest names (into the vault), and the settings per service (checked against its config schema).
11. **Platform review** — name, version, runtime; services with their profiles; dedicated agent sockets, highlighted; training jobs with their image digests and caps; restricted content packages; state directories and which are backed up; required facilities, optional tools, relaxations. Continue only after explicit approval.
12. **Golden images** — checklist of blueprints with estimated build time and disk usage; build now or later; VM variants offered only if `vm_targets`.
13. **Secrets & vault** — all service passwords generated automatically by default (listed by name, values not shown); Grafana admin password generate/enter; vault password entered twice with a strength meter, or a path to an existing vault password file (must be 0600). Optional root-only storage of the vault password in `/etc/pinka/secrets/vault-pass`, with the trade-off explained.
14. **Review** — every setting grouped by section, each with a jump-back link; a diff view for modified files; the list of files to be written; the capacity plan's totals and estimates for downloads (container images, model GB) and duration; destructive actions highlighted in red. Buttons: Back, Export answers only, Install.
15. **Install progress** — a phase tree (preflight gate → provider → base → … → doctor) with per-role status icons, current task, elapsed time, and ok/changed/failed counts; a collapsible live log pane (filtered; full log path shown); the Tailscale login URL and QR code when needed.
    - On failure: failing task, stderr excerpt, remediation hint, buttons Retry phase / Open log / Abort.
    - On signal 75 (WSL restart): a full-screen instruction — "In Windows PowerShell run `wsl --terminate <distro>`, then open WSL and run `sudo ./bootstrap/bootstrap.sh` again; the installer resumes here."
    - On signal 76 (reboot / MOK): reboot instructions including the MOK Manager steps.
    - State is saved *before* these screens are shown.
16. **Doctor** — results grouped by category with pass/warn/fail and remediation; an optional *Run smoke test* action.
17. **Finish** — connection info per researcher (`ssh <user>@<tailscale-hostname>`), how to reach Grafana, the vLLM endpoint and the model being served, files to commit (with the commit offer), log locations, how to start/stop the lab.

### 18.5 Ansible progress integration

- A custom callback plugin `ansible/callback_plugins/pinka_events.py` emits JSON lines (play start, role start, task start/end with status, changed flag and duration, warnings, failures with message and truncated stderr) to `/run/pinka/installer/events.jsonl` or a passed file descriptor. It must respect `no_log`: never emit args or results of `no_log` tasks.
- The regular human-readable output still goes to the bootstrap log file.
- Bootstrap signals (§5.2 step 8) also appear as events.
- Non-TUI runs may enable the same plugin (useful for CI logs).

### 18.6 Answers file

- Schema: `installer/schema/answers.schema.json`. Contains every non-secret choice, including *which* secrets are generated versus provided, but never their values. Platform choices are the `platform` and `content` source blocks, `platform_settings`, `gpu_arbiter` and `training_caps` of §8.1. `gpu.accept_mok_enrollment` records the consent of §3.2; the MOK password is never in the file. The capacity choices (concurrent targets, retention settings, pool source and size) and `tailscale_serve_consent` are answers fields; uninstall is never replayed from an answers file.
- Replay: `sudo ./bootstrap/bootstrap.sh --answers installer-answers/<machine>.yml --non-interactive --vault-password-file <file>` reproduces the install.
- For a first non-interactive install with no vault yet, secrets come from environment variables `PINKA_SECRET_<NAME>` or are generated and written into a new vault whose password comes from `--vault-password-file`.

### 18.7 Secret handling

- Masked inputs (paste allowed), never echoed, never logged; a log redaction filter acts as a second line of defense.
- Secrets live in memory only until written into the vault through Ansible's vault API. Generated secrets use `secrets.token_urlsafe`.
- "Shown once" secrets require an explicit acknowledgement; the screen is cleared afterwards. Warn about terminal scrollback and tmux logging.
- The MOK password is passed to `mokutil` via stdin only.
- One masked field per secret the manifest names; the values go into the vault as `vault_platform_secrets.<service>.<name>`.

### 18.8 Validation

All rules live in `validators.py` and are unit-tested: hostname, Linux username, absolute paths, cpusets, sizes, URLs, SSH public keys, HF repo IDs and 40-hex revisions, git refs, restic repository URLs, Tailscale tags, the concurrent targets and retention values of the capacity plan, and uninstall's typed confirmations.

### 18.9 Tests

- Textual pilot tests (`App.run_test()`) for complete flows: fresh WSL2 with GPU; bare metal with a dedicated pool disk; degraded host without GPU; restore mode; modify mode; validation error paths; resume after a simulated signal 75.
- Snapshot tests (pytest-textual-snapshot) for every screen at 80×24 and 120×40, dark and light themes.
- Runner tests using fake preflight JSON and a recorded `events.jsonl` replay.
- Answers round-trip: answers → files → reload → identical answers.
- Snapshots of the new and changed screens (Platform, Platform review, the GPU choices of the capacity plan) and a pilot run of the whole flow with the reference platform's manifest.
- Snapshots of the capacity plan for fits, degraded and does not fit; pilot runs of the uninstall flow (dry run, backup offer, typed confirmations) and of the Remote access screen on an adopted Tailscale node.

### 18.10 Look and feel (guidance, not a pixel spec)

```
┌ Pokus Infrastructure Installer ──────── step 5/17 · Storage device ┐
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

A `platform_facts` role runs first and sets: `pinka_family`, `pinka_distro`, `pinka_release`, `pinka_parent_release` (for derivatives), `pinka_release_model`, `pinka_pkg_manager`, `pinka_service_names` (ssh, time sync, …), `pinka_initramfs_tool`, `pinka_firewall_backend`, `pinka_mac` (`selinux` / `apparmor` / `none`), `pinka_version_hold_method`. No other role may branch on `ansible_distribution` directly — a lint rule (custom ansible-lint rule or grep check in CI) enforces this.

Everything else is distro-independent by construction:

- Lab services run as **containers pinned by digest** — identical on every host.
- Platform services run in the **uv-managed Python environment** — identical on every host.
- **Target environments are Incus instances built from our own golden images** — the userland of a target is the same no matter which distro the host runs. Only the host kernel differs, and it is recorded in the fingerprint.

### 19.2 Supported matrix (summary; the YAML in §2.3 is authoritative)

| Tier | Distributions | Meaning |
|---|---|---|
| supported | Ubuntu 24.04 LTS, Debian 13, Ubuntu 26.04 LTS (pending verification until its first passing `make test-distros` result; its Incus comes from universe, which has no guaranteed security updates) | Full CI VM tests + reference GPU hardware |
| tested | Ubuntu 22.04, Debian 12, Fedora (current and previous), RHEL / AlmaLinux / Rocky / CentOS Stream 9 and 10, openSUSE Leap 15.6 / 16, openSUSE Tumbleweed, Arch Linux | Full CI VM tests (`make test-distros`); GPU paths not hardware-tested |
| experimental | Derivatives via `ID_LIKE` (Linux Mint, Pop!_OS, Zorin, elementary, Kali, Oracle Linux, Manjaro, EndeavourOS, …), SLES, `aarch64` hosts | Uses the parent family; consent required |
| unsupported | RHEL-compatible 8 (no unified cgroup v2 by default), non-systemd (Alpine, Devuan, Void, Gentoo/OpenRC, Slackware), musl, image-based/immutable (Fedora Atomic/CoreOS, openSUSE MicroOS/Aeon, Ubuntu Core, NixOS), containers, WSL1, other architectures | Clear message + the **VM escape hatch**: run a supported distro in a VM on that host (`vm` provider; GPU via passthrough if available) |

Verify every listed release at build time (EOL status, availability of Docker, Incus, NVIDIA toolkit, Tailscale) and adjust tiers in the matrix, telling me what changed and why.

### 19.3 Python independence

- Bootstrap installs **uv** (static binary, checksum from `versions.lock.yml`) and then a **pinned CPython** via `uv python install` into `/opt/pinka/python`. Ansible, the TUI, `pinka` and the platform use it. The distro's Python is never required for our code.
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
- A component's source may be overridden for one release when its family default does not serve it: `incus` on Ubuntu 26.04 is the distribution's own package from universe (§7.7).
- A pinned version may differ per architecture (§10.1).

### 19.5 Updates, holds and rolling releases

| Family | Version hold | Automatic security updates (optional, default on for fixed releases) |
|---|---|---|
| debian | apt preferences pins in `/etc/apt/preferences.d/pinka-pins` | `unattended-upgrades`, security origin only, no reboot |
| redhat | `dnf versionlock` (plugin installed by the role) | `dnf-automatic` with `upgrade_type = security`, no reboot |
| suse | `zypper addlock` | systemd timer running `zypper --non-interactive patch --category security` (verify), no reboot |
| archlinux | `IgnorePkg` in a managed `pacman.conf` include | none — rolling updates are manual via `pinka update-host` |

- `pinka update-host` (all families): runs the platform's `drain` hook and asks the GPU arbiter for `idle`, stops the lab, updates the OS with the family's tool while keeping holds, runs bootstrap (idempotent), runs doctor, and prints the fingerprint diff. This is the only supported way to update a host.
- On `rolling` distros, doctor warns when the running kernel or held components drift from the session fingerprint, and reports must show the fingerprint diff when a campaign spans an update.

### 19.6 Firewall backends

Selected automatically by `platform_facts`: `firewalld` if active, else `ufw` if active, else `nftables`. Override with `firewall_backend` in host_vars. Required effective policy (identical for all backends, verified by doctor with connection tests, not by reading rules):

- Inbound SSH allowed only on `tailscale0` (+ `ssh_extra_allowed_interfaces`).
- Grafana serve port allowed only on `tailscale0` if enabled.
- Incus bridges: DHCP/DNS from instances to the host's Incus dnsmasq; forwarding between instances inside the same run network; no forwarding from run networks to anything else.
- Docker/Incus coexistence fixed (`DOCKER-USER` acceptance for Incus bridges, or the backend's equivalent).

Backend specifics:

- `firewalld`: a dedicated zone `pinka-tailnet` for `tailscale0` with the ssh service; Incus bridges placed in the `trusted` zone as Incus documents for firewalld hosts (verify); Docker's own `docker` zone left intact.
- `ufw`: `ufw allow in on tailscale0 to any port 22 proto tcp`; route/allow rules for Incus bridges as Incus documents for ufw (verify); Docker coexistence noted.
- `nftables`: own `inet pinka_filter` table via `pinka-nftables.service`.

### 19.7 Mandatory access control (`mac` role)

- **SELinux** (Fedora, RHEL-compatible, possibly openSUSE): never set permissive or disabled. Label `/srv/pinka` paths with persistent `semanage fcontext` rules + `restorecon`; bind mounts into containers use `:z`/`:Z` (shared vs private) correctly; enable Docker's `selinux-enabled`; install required policy packages (`container-selinux` etc., per family vars). Verify Incus's behavior on SELinux-enforcing hosts and document limitations. Doctor's smoke test fails on new AVC denials attributed to lab components during the test window.
- **AppArmor** (Ubuntu, Debian, openSUSE Leap): rely on Docker's and Incus's own profiles; nothing to disable.
- The detected MAC system and mode are part of the fingerprint.

### 19.8 GPU portability

- **Driver installation methods by family** (`install_nvidia_driver: auto`): Ubuntu — pinned driver branch packages; Debian — `nvidia-driver` from non-free per release docs; Fedora — RPM Fusion `akmod-nvidia` (community repo consent, akmods Secure Boot key enrollment); RHEL-compatible — NVIDIA's official repository with precompiled or DKMS modules (verify current recommendation); openSUSE — NVIDIA's official repository (open kernel module packages; verify naming); Arch — `nvidia-open` or `nvidia-open-dkms`. Only Ubuntu and Debian `auto` installs are `supported`; all others are `tested`/`experimental` and the TUI recommends `manual` if the operator prefers the distro's own documented procedure.
- **Secure Boot:** MOK flow with the operator's consent (§3.2; Ubuntu, Debian, openSUSE, RHEL-compatible with DKMS), akmods key flow (Fedora), manual `sbctl` signing on Arch (`needs_action` with docs). Never disable Secure Boot.
- **Container GPU access:** CDI first (§7.6), identical on all distros and on WSL2.
- **Training containers** get the GPU through CDI as vLLM does, WSL2 included (§7.18). Whether NVIDIA's CDI hooks work with a read-only container root file system is a verification item.
- **GPU vendors:** NVIDIA is implemented. The `gpu` detector and the `vllm` role are written behind a `gpu_backend` interface (`nvidia`, `none`) with a documented, unimplemented slot for `amd` (ROCm vLLM image, `/dev/kfd` + `/dev/dri`). AMD GPUs are reported by preflight as present-but-unsupported.
- **No GPU at all:** the lab installs fully (degraded: no local inference). See §19.10 for using a GPU elsewhere.

### 19.9 Proving portability: `make test-distros`

- A harness that launches **disposable Incus VMs** (on a developer machine or the lab host, explicitly invoked, never in the middle of experiments) for every `supported` and `tested` distro image, copies the repo in, runs `bootstrap.sh --non-interactive --answers tests/answers/ci.yml --accept-degraded`, then runs bootstrap a second time (must be `changed=0`), then `pinka doctor --smoke` (nested Incus containers inside the VM), and collects logs and the host profile.
- Results are written to `docs/support-matrix.md` (generated) with the test date, distro release, and pass/fail per stage. A distro whose last test failed or is older than 90 days is shown as such.
- Variants: one VM per firewall backend (nftables, firewalld, ufw), one SELinux-enforcing run (Fedora + Alma), one derivative (if an image is available).
- The reference platform (§7.18) is deployed in each VM: isolation probe (every forbidden action must fail), the training flow (dataset, job, adapter, vLLM serving it on GPU hosts; the CPU-only variant elsewhere), an interrupted job resumed after reboot, doctor, a second deployment with `changed=0`, a replacement by a renamed copy, backup and restore.
- Every VM is sized to the computed requirement of the configuration it installs (§2.3), never below preflight's minimums, and the VMs run one at a time.
- Lane `uninstall`, per target: a snapshot before install (packages and versions, holds, repositories and keys, users and groups, enabled units, `nft list ruleset`, listening ports, files below pinka's paths, Tailscale status and preferences, Docker containers, networks and volumes), install, uninstall, the same snapshot, and a failure on any difference not listed as kept on purpose. Variant `ubuntu-24.04-preexisting`: the harness installs Docker and Tailscale first (logged in with the test tailnet's key and its own tags) and runs a workload container; the node's identity and the workload's connectivity must be unchanged after install and after uninstall.
- Ubuntu 26.04 is a target; its first passing result clears the support matrix's `pending_verification` (the generator reads `tests/distros/results.yml`, never a hand edit).
- Fast lane for CI without nested virtualization: `ansible-playbook --check` against systemd-enabled containers per family for the provider-agnostic roles, plus all preflight fixtures. This is the required job; a separate, non-blocking extended job runs the whole play in check mode with the reference platform enabled (§11).

### 19.10 Hardware portability: split inference (`node_role`)

To make the lab usable on machines without a suitable GPU, a host can take one of three roles (`node_role` in host_vars):

- `full` (default): everything on one host.
- `lab`: everything except vLLM. The platform's controller and inference broker reach a remote inference node through infra's inference forwarder on the loopback (§12.8), which alone holds the mutual-TLS client certificate and dials the node over the tailnet.
- `inference`: only the inference stack — vLLM, GPU exporters, OTel forwarding to the lab node's tailnet OTLP listener (mutual TLS), and a small **inference control endpoint** (`pinka-inference-control`, on the node's tailnet address, mutual TLS with certificates generated on both nodes during pairing, plus a tailnet ACL rule; vLLM itself stays on `127.0.0.1:18704` and the endpoint forwards its API, §12.11) that exposes exactly five requests: `GET /health` (`ok`, `vllm_status`, `deployment`, and `fingerprint` with `hash` and `computed_at`); `GET /model` (`deployment`, `models` and the served model's verified `MANIFEST.json` as `manifest`); `GET /metrics` (the GPU and vLLM metrics, passed through); `POST /activate` with `{"deployment": "<id>@<version>"}`, sent to the inference node's GPU arbiter as `serve <id>@<version>` and answered with the arbiter's result; and `POST /verify` (the serving verification's report, §7.11). Nothing else: no `train`, `idle` or `publish-dataset`.

Rules:

- The TUI offers pairing: on the inference node it displays a one-time pairing code; on the lab node the operator enters it; both exchange certificates over the tailnet (port 18710, while the control endpoint is stopped).
- Model weights live on the inference node; registry hashes are verified there and reported back.
- Both nodes' fingerprints are recorded for every run (`inference_fingerprint_hash`), and reports never compare latency across different inference placements.
- The inference node runs the GPU arbiter for serving only. Training runs only on `full` nodes: on a `lab` and `inference` pair the installer and doctor state that training is unavailable, and split-node training is recorded as future work.
- Still no external APIs: an inference node must be a machine provisioned by Pokus Infrastructure that you control.

### 19.11 Portability documentation

- `docs/distros/<family>.md` for each family: sources used, holds, update policy, firewall and MAC notes, known issues, NVIDIA driver notes.
- `docs/distros/adding-a-family.md`: the exact checklist to add a new family (support-matrix entry, component sources, family vars for every role, fixtures, test-distros image).
- `docs/node-roles.md`: full / lab / inference, pairing, and measurement caveats.
