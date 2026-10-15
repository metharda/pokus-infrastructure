---
title: How to troubleshoot a bootstrap run
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Find out why bootstrap stopped, by exit code, and what to do before running it again.
---

# How to troubleshoot a bootstrap run

`bootstrap.sh` always ends with an exit code from the
[exit code table](../reference/exit-codes.md#bootstrapsh). This page has one
section per code. Every stage of bootstrap is idempotent, so after you have
dealt with the cause you run the same command again and it resumes.

!!! note "Prerequisites"
    - A host on which `sudo ./bootstrap/bootstrap.sh` has run and stopped.
    - Root access on that host (`sudo`).
    - Your pokus-infrastructure checkout on that host.

## Find the exit code and the log

1. Read the last lines bootstrap printed. They name the exit code, its
   meaning, and the log file, for example:

    ```text title="Output"
    2026-10-05T09:12:44Z [ERROR] bootstrap ended with exit code 77: operator action required: ...
    2026-10-05T09:12:44Z [ERROR] see docs/how-to/troubleshoot.md and the log /var/log/pinka-bootstrap/20261005T091201Z.log
    ```

2. If the terminal output is gone, open the newest log. Logs are readable by
   root only:

    <!-- qa:run env=E2 -->
    ```bash
    sudo ls -t /var/log/pinka-bootstrap/
    sudo less "/var/log/pinka-bootstrap/<log-file>"
    ```

    `<log-file>` is the first name the `ls` command lists.

3. The state file records the exit code, the stage in which bootstrap stopped
   and, for a signal from the play, its message. Bootstrap does not write it
   in check mode (`--check`):

    <!-- qa:run env=E2 -->
    ```bash
    cat /etc/pinka/bootstrap-state.json
    ```

Then go to the section of that exit code.

## Exit code 0: success {#exit-code-0}

Bootstrap finished and `pinka doctor` reported no warnings. Nothing to do.

## Exit code 1: doctor warnings {#exit-code-1}

Provisioning finished; `pinka doctor` found warnings. Each warning in the
doctor summary comes with a remediation, and
[How to fix a failed doctor check](fix-doctor-checks.md) has a section per
check ID. Act on the ones that matter for your lab, then check again:

<!-- qa:run env=E2 -->
```bash
sudo pinka doctor
```

## Exit code 2: doctor failures {#exit-code-2}

Provisioning finished, but `pinka doctor` reports failed checks; bootstrap
also reports 2 if doctor itself ended unexpectedly. Fix each failed check as
its remediation and its section in
[How to fix a failed doctor check](fix-doctor-checks.md) say, then run
bootstrap again (see
[Verify the fix](#verify-the-fix)) so that the play can repair what it manages.

## Exit code 10: degraded {#exit-code-10}

Preflight found that optional features are unavailable, for example no usable
GPU or no KVM, and nobody consented to continue without them. Bootstrap
printed each disabled feature with its consequence for research.

- If the missing feature can be provided (a driver, nested virtualization),
  provide it and run bootstrap again.
- If the lab is useful without it, run bootstrap again with
  `--accept-degraded`, or answer `y` when bootstrap asks. The consent is
  recorded in the host profile.

## Exit code 11: experimental {#exit-code-11}

The distribution or the CPU architecture is on the experimental tier (see the
[support matrix](../support-matrix.md)) and nobody consented. Install a
supported release, or run bootstrap again with `--accept-experimental` (or
answer `y` when it asks); the consent is recorded in the host profile.

## Exit code 20: unsupported {#exit-code-20}

A core requirement failed, or the operating system, architecture or
environment is unsupported, or a blocking conflict exists. The reasons are
printed above the exit line and stored in `unsupported_reasons` of the host
profile. Fix the cause, for example by installing a supported distribution or
by running the lab in a VM on this machine. Only if you accept running
outside the support matrix, run bootstrap again with `--force-unsupported`;
this is recorded in the host profile and in every environment fingerprint.

## Exit code 30: needs action {#exit-code-30}

An operator action is required before bootstrap can continue. Bootstrap
printed the exact remediation, for example:

- install `curl` (or `wget`), `tar` or `git` with the command it printed;
- enable systemd for a WSL distribution in `/etc/wsl.conf`, then restart the
  distribution as described for [exit code 75](#exit-code-75);
- remove a conflicting installation named in the preflight summary;
- `secure_boot_mok`: Secure Boot is on and no NVIDIA driver is installed.
  Consent to the MOK enrollment (`--accept-mok-enrollment`, the installer's
  Preflight screen, or `gpu.accept_mok_enrollment: true` in an answers file),
  install the driver yourself, or set `install_nvidia_driver: skip`; see
  [Bare-metal hosts](../providers/baremetal.md#secure-boot-and-reboots).
- `lab_port_in_use` or `vm_collector_address_conflict`: another program uses
  a lab port or the VM collector address. Stop it, or
  [move the lab's port or address](move-a-lab-port.md).

Do what it says, then run bootstrap again. `--force-unsupported` does not skip
a required action.

## Exit code 40: preflight error {#exit-code-40}

Host detection failed unexpectedly; the Python traceback is in the log. Run
preflight alone to reproduce it without changing anything:

<!-- qa:run env=E2 -->
```bash
cd <checkout>/infra
sudo ./preflight/pinka-preflight --no-write --output /tmp/host-profile.json
```

`<checkout>` is the directory where you cloned the repository. Report
the traceback together with the output of `cat /etc/os-release`.

## Exit code 50: tooling failed {#exit-code-50}

Installing the pinned uv, CPython, the Ansible environment or the Ansible
collections failed. The lines above the exit line name the step.

- A download failed: check that the host reaches `github.com`,
  `release-assets.githubusercontent.com`, `releases.astral.sh` and `pypi.org`
  (preflight lists unreachable endpoints), then run bootstrap again.
- A checksum did not match: do not work around it. The download is not the
  file that `versions.lock.yml` pins; check for a proxy that rewrites
  downloads, and report it if the problem stays.
- An Ansible collection cannot be downloaded: the line names the collection
  and its URL from `ansible_collections.artifacts` of `versions.lock.yml`;
  check that the host reaches `galaxy.ansible.com`. A pin that is still
  `TODO(verify)` is never downloaded: this release has not pinned that
  artifact yet, so report it.
- The artifact of a collection is refused (the line before names the
  member and why): it carries an absolute or escaping symbolic link, a hard
  link, a device or a FIFO, or its `MANIFEST.json` or `FILES.json` does not
  match its files. The pinned artifact is not what Galaxy built; report it.
- The installed collections do not match their `FILES.json`: bootstrap
  found a changed file below `/opt/pinka/ansible-collections`, installed the
  pinned artifacts again, and the check still failed right after. Something
  on the host changes the files as they are written; report it.

## Exit code 60: play failed {#exit-code-60}

The Ansible play failed without a bootstrap signal, or `pinka` is not
installed after the play. The failing task and its error are in the log, just
above the play recap. Fix the cause and run bootstrap again; tasks that already
succeeded report no changes.

## Exit code 64: usage error {#exit-code-64}

The command line was invalid; the message names the option. List the options:

<!-- qa:run env=E1 -->
```bash
./bootstrap/bootstrap.sh --help
```

See also the [bootstrap.sh reference](../reference/cli/bootstrap.md).

## Exit code 65: platform release refused {#exit-code-65}

The platform release or its sources break contract v2. Bootstrap printed
every reason, one per line, as `<where>: <what> [<rule>]`; the deployment
changed nothing on the host, and a running platform keeps running. Fix the
cause and run bootstrap again:

- A rule of the manifest or the release (for example `secrets-forbidden`,
  `command-missing`): fix the release and tag a new version.
  [Platform manifest](../reference/platform-manifest.md) and
  [Platform privilege profiles](../reference/platform-profiles.md) describe
  every rule.
- `platform-active`: another platform is active. A release with a new name
  replaces it only with `--replace-platform <active name>`; see
  [How to deploy a platform](deploy-platform.md).
- `source-*`, `content-*`, `tag-signature`, `moved-tag`: the `platform` and
  `content` settings in the host variables, or the repository, are wrong:
  an unknown ref or path, overlapping paths, an undeclared or missing
  content package, an unsigned tag, or a tag that now names another commit.
- `build-modified-release`: the release's build changed files of the release
  itself; the release must build without that.

To see the problems and the summary without deploying, run bootstrap with
`--inspect-platform`.

## Exit code 70: internal error {#exit-code-70}

A command failed that bootstrap did not expect to fail. The message names the
command and its location (`file:line`). Run bootstrap again once; if the error
repeats, report it together with the log.

## Exit code 75: WSL restart required {#exit-code-75}

The WSL distribution must restart, for example after systemd was enabled in
`/etc/wsl.conf`. Bootstrap cannot do this from inside because Windows interop
is disabled by design. In a Windows terminal run:

<!-- qa:skip reason="runs on the Windows side, not on the lab host" -->
```powershell
wsl --terminate <distro>
```

`<distro>` is the name of the WSL distribution; `wsl --list` shows it. Open the
distribution again and run bootstrap again.

## Exit code 76: reboot required {#exit-code-76}

A driver or kernel change needs a reboot, or a Machine Owner Key (MOK)
enrollment is pending; when bootstrap imported the key itself, it says so.
Reboot the host. If the blue MOK Manager screen appears, choose *Enroll MOK*,
*Continue* and *Yes*, and enter the one-time MOK password you chose. Then run
bootstrap again.

Bootstrap never reboots on its own. If you started it with `--allow-reboot`,
it schedules the reboot itself (`shutdown -r +1`) after writing its state
file, and still ends with exit code 76; cancel a scheduled reboot with
`shutdown -c`. In check mode it never reboots.

## Exit code 77: operator action required {#exit-code-77}

Bootstrap stopped and printed what to do. Common messages:

| Message | What to do |
| --- | --- |
| must run as root | Run it with `sudo`. |
| must run from a git checkout | Run it from a clone of the repository, not from a copy. |
| must be owned by root or by the user running sudo | Clone the repository as your own user or as root. |
| infra/ has uncommitted changes | Commit them, or add `--allow-dirty` (recorded). |
| --ref ... is not the checked-out commit | Check out that ref first, or leave out `--ref`. |
| the host profile changed since the last bootstrap | Read the listed changes; if they are expected, add `--refresh-profile`. |
| host_vars/... does not exist | Create `ansible/inventory/host_vars/<machine>.yml` for this machine. |
| the vault needs a password | Add `--vault-password-file <file>`. |
| versions.yml does not match versions.lock.yml | Run `make generate` in `infra/` and commit the result. |
| another bootstrap is running | Wait for the other run to finish. |
| the installer TUI cannot start | The terminal is smaller than 80x24, `TERM` is unset or `dumb`, or there is no terminal: use a larger terminal, or [an answers file](install-without-the-tui.md). |
| the answers file ... cannot be used | Fix every field listed above it (see the [answers file reference](../reference/answers.md)). |
| --answers needs --vault-password-file | Add `--vault-password-file <file>`; for a new lab, that password encrypts the new vault. |
| the answers could not be applied | Read the reason above it: a wrong vault password, or a secret the answers call *provided* is missing from `PINKA_SECRET_<NAME>`. |
| Stopped before the installation finished | You quit the installer TUI; run `sudo ./bootstrap/bootstrap.sh` again and choose *Resume*. |
| The host changed since bootstrap validated it | The play's preflight gate found a different outcome, provider or feature set; check the listed changes, then add `--refresh-profile`. |
| the planned MOK enrollment needs a one-time password | Run bootstrap on a terminal, where it asks for the password, or pass it on standard input with `--mok-password-stdin`. |
| ... must be imported for enrollment and bootstrap has no one-time MOK password | Run `sudo mokutil --import <key>` with the key it names and choose a one-time password, reboot, enroll the key, then run bootstrap again. |
| mokutil --import ... failed | The same as the previous line; the error of `mokutil` is above the message. |
| install_nvidia_driver is ..., but bootstrap ran preflight with ... | Write `install_nvidia_driver` in host_vars as a plain value (`auto`, `manual` or `skip`) that bootstrap can read, then run bootstrap again. |
| install_nvidia_driver is ...; it must be auto, manual or skip | Correct the value in `ansible/inventory/host_vars/<machine>.yml`. |
| No usable NVIDIA driver is loaded | Install the driver as your distribution documents it, or set `install_nvidia_driver: skip` in host_vars. See [Bare-metal hosts](../providers/baremetal.md). |
| The NVIDIA driver for this distribution comes from a community repository | Add `--allow-community-repos` (recorded in the fingerprint), or install the driver yourself with `install_nvidia_driver: manual`. |
| Tailscale needs a login | Put an auth key into `vault_tailscale_authkey` in the vault, or run bootstrap interactively and open the login URL it prints. |

`<machine>` is the name you pass with `--machine`; `<file>` is a file that
contains the vault password; `<key>` is the key file the message names.

## Verify the fix

1. Run bootstrap again with the same options:

    <!-- qa:skip reason="provisions the host; covered by the installation tutorials" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine>
    echo "exit code: $?"
    ```

    `<machine>` is the name you passed before.

2. Check that it now ends with exit code 0 (or 1 if you accept the remaining
   doctor warnings), and that the state file says so:

    <!-- qa:run env=E2 -->
    ```bash
    grep '"result"' /etc/pinka/bootstrap-state.json
    ```

    ```text title="Output"
      "result": "success",
    ```
