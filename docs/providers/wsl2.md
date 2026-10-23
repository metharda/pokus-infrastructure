---
title: WSL2 hosts
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What the wsl2 provider configures, how WSL restarts work, the recommended .wslconfig and known limitations.
---

# WSL2 hosts

A WSL2 distribution on a Windows workstation is a supported lab host. Pokus Infrastructure (pinka)
treats it as a Linux host that happens to run on Windows: nothing in the lab
calls Windows programs, and bootstrap never changes anything on the Windows
side. The `provider_wsl2` role applies when preflight detects WSL2. WSL1 is
not supported; convert the distribution with `wsl --set-version <distro> 2`
on the Windows side, where `<distro>` is the name `wsl --list` shows.

## What the provider configures

`/etc/wsl.conf` is managed by Pokus Infrastructure:

| Section | Setting | Value | Why |
| --- | --- | --- | --- |
| `[boot]` | `systemd` | `true` | the lab runs systemd services |
| `[automount]` | `enabled`, `mountFsTab` | `false` | Windows drives are not mounted into the lab |
| `[interop]` | `enabled`, `appendWindowsPath` | `false` | Linux processes cannot start Windows programs |
| `[network]` | `hostname` | `machine_name` | the same name in the tailnet and the fingerprint |
| `[network]` | `generateHosts`, `generateResolvConf` | `true` | WSL keeps managing name resolution |

The values are role variables (`provider_wsl2_*` in
[Ansible variables](../reference/ansible-variables.md#provider_wsl2)); only
change them if you know why.

The GPU is provided by the Windows NVIDIA driver through `/dev/dxg` and the
libraries in `/usr/lib/wsl/lib`, which the provider adds to the dynamic linker
path. **Pokus Infrastructure never installs a Linux NVIDIA kernel driver under WSL2**, and
preflight refuses a host where one is installed, because it would replace the
libraries WSL provides.

The Windows driver must support the CUDA version the pinned vLLM image is
built on; when it is older, preflight turns `gpu_inference` off and asks you
to update the NVIDIA driver on Windows, and doctor repeats the check
([`gpu.driver_cuda`](../how-to/fix-doctor-checks.md#check-gpu-driver_cuda)).

The GPU needs compute capability 7.5 or newer, the oldest the pinned vLLM
image has kernels for (approved deviation V7 from the build prompt's 7.0).
Preflight refuses GPU inference on an older GPU: `gpu_inference` stays off,
and no option turns it on.

Containers reach the GPU through CDI. The NVIDIA Container Toolkit's
`nvidia-cdi-refresh` units write `/var/run/cdi/nvidia.yaml` in WSL mode
whenever the Windows driver or the toolkit changes, so the specification
names `/dev/dxg` and the libraries in `/usr/lib/wsl/lib` instead of Linux
driver devices. vLLM and training job containers get the GPU the same way:
the GPU arbiter starts a job's container with the CDI device
`nvidia.com/gpu=all` only while it trains that job, and with Docker's NVIDIA
runtime only on a Docker release without CDI support. Without either, only
CPU-only jobs (`resources.gpus: 0`) run
([How to run a training job](../how-to/run-training-job.md)).

## Restarting the distribution

When `/etc/wsl.conf` changes, or systemd is not running as PID 1, the
distribution must be restarted, which only Windows can do. Bootstrap stops
with exit code 75 and prints the command. In a Windows terminal run:

<!-- qa:skip reason="runs on the Windows side, not on the lab host" -->
```powershell
wsl --terminate <distro>
```

`<distro>` is the distribution name that bootstrap prints (or that
`wsl --list` shows). Open the distribution again and run the same bootstrap
command; it continues where it stopped.

## Recommended `.wslconfig`

The memory, CPUs and swap of the WSL virtual machine are set in
`%UserProfile%\.wslconfig` on Windows, which Linux cannot change. The provider
renders a recommendation for this machine into
`/srv/pinka/services/contrib/wslconfig.recommended`; copy it to Windows
yourself and restart WSL with `wsl --shutdown`. It contains:

| Setting | Value | Why |
| --- | --- | --- |
| `memory` | `expected.memory_gib` from host_vars, else 18 GB | vLLM and the targets need most of the host's memory; preflight needs 16 GiB of `MemTotal`, which the kernel's reservations keep below this value |
| `processors` | `expected.cpu_threads` from host_vars, else 8 | `cpuset_services` and `cpuset_targets` assume these CPUs |
| `swap` | 8 GB | a small swap keeps memory pressure from killing services |
| `nestedVirtualization` | `true` | VM targets need `/dev/kvm` inside WSL |
| `localhostForwarding` | `false` | lab services listen on `127.0.0.1` inside WSL and must not appear on Windows' loopback |

Doctor warns when the effective values inside WSL differ from the
expectations in host_vars. A template with comments is in
`contrib/windows/wslconfig.template`.

## Keeping the distribution running

Windows stops an idle WSL distribution and may let the workstation sleep.
The optional helper `contrib/windows/pinka-wsl.ps1` is a PowerShell profile
function that starts a hidden keep-alive process in the distribution and asks
Windows not to sleep while it runs. It is never executed by bootstrap and
nothing in the lab depends on it; read the comments in the file before you
add it to your PowerShell profile.

## Known limitations

- **No DCGM.** NVIDIA's DCGM does not support WSL2; GPU metrics come from an
  `nvidia-smi`-based exporter.
- **Nested virtualization.** VM targets need `/dev/kvm`, which depends on the
  Windows version, the CPU and `nestedVirtualization`. Without it preflight
  reports `vm_targets` as disabled and the lab runs container targets only.
- **The Windows side is not observable.** The Windows build, its drivers and
  other Windows load are outside the fingerprint. Measurements include the
  overhead of WSL's virtual machine; reports keep WSL2 results apart from
  bare-metal results.
- **Power.** The distribution stops when Windows shuts down or the
  workstation sleeps. Runs in progress are marked invalid and resumed by the
  platform after the next start.

Related: [Portability](../explanation/portability.md),
[ADR-0003 (provider model)](../adr/0003-provider-model.md).
