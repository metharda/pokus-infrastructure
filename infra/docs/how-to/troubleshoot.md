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
    - Your Pokus checkout on that host.

## Find the exit code and the log

1. Read the last lines bootstrap printed. They name the exit code, its
   meaning, and the log file, for example:

    ```text title="Output"
    2026-10-05T09:12:44Z [ERROR] bootstrap ended with exit code 77: operator action required: ...
    2026-10-05T09:12:44Z [ERROR] see docs/how-to/troubleshoot.md and the log /var/log/pokus-bootstrap/20261005T091201Z.log
    ```

2. If the terminal output is gone, open the newest log. Logs are readable by
   root only:

    <!-- qa:run env=E2 -->
    ```bash
    sudo ls -t /var/log/pokus-bootstrap/
    sudo less "/var/log/pokus-bootstrap/<log-file>"
    ```

    `<log-file>` is the first name the `ls` command lists.

3. The state file records the exit code, the stage in which bootstrap stopped
   and, for a signal from the play, its message. Bootstrap does not write it
   in check mode (`--check`):

    <!-- qa:run env=E2 -->
    ```bash
    cat /etc/pokus/bootstrap-state.json
    ```

Then go to the section of that exit code.

## Exit code 0: success {#exit-code-0}

Bootstrap finished and `pokus-lab doctor` reported no warnings. Nothing to do.

## Exit code 1: doctor warnings {#exit-code-1}

Provisioning finished; `pokus-lab doctor` found warnings. Each warning in the
doctor summary comes with a remediation. Act on the ones that matter for your
lab, then check again:

<!-- qa:run env=E2 -->
```bash
sudo pokus-lab doctor
```

## Exit code 2: doctor failures {#exit-code-2}

Provisioning finished, but `pokus-lab doctor` reports failed checks; bootstrap
also reports 2 if doctor itself ended unexpectedly. Fix each failed check as
its remediation says, then run bootstrap again (see
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
- remove a conflicting installation named in the preflight summary.

Do what it says, then run bootstrap again. `--force-unsupported` does not skip
a required action.

## Exit code 40: preflight error {#exit-code-40}

Host detection failed unexpectedly; the Python traceback is in the log. Run
preflight alone to reproduce it without changing anything:

<!-- qa:run env=E2 -->
```bash
cd <pokus-checkout>/infra
sudo ./preflight/pokus-preflight --no-write --output /tmp/host-profile.json
```

`<pokus-checkout>` is the directory where you cloned the repository. Report
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

## Exit code 60: play failed {#exit-code-60}

The Ansible play failed without a bootstrap signal, or `pokus-lab` is not
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
enrollment is pending. Reboot the host. If the blue MOK Manager screen
appears, choose to enroll the key and confirm it with the MOK password set
during provisioning. Then run bootstrap again.

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
| this version does not include the installer TUI yet | Use `--no-tui`, and leave out `--answers`. |

`<machine>` is the name you pass with `--machine`; `<file>` is a file that
contains the vault password.

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
    grep '"result"' /etc/pokus/bootstrap-state.json
    ```

    ```text title="Output"
      "result": "success",
    ```
