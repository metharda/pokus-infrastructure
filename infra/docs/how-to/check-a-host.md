---
title: How to check whether a machine can host the lab
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Run the read-only preflight check on a candidate machine and read its verdict.
---

# How to check whether a machine can host the lab

Run `pokus-preflight` on a machine before you install anything, to find out
whether the lab is supported there and what would be missing.

!!! note "Prerequisites"
    - A Linux machine (bare metal, a VM or WSL2) with Python 3.9 or newer.
    - A copy of the Pokus repository on that machine.
    - Network access, unless you only want the local checks.

## Run the check

1. Go to the infra component of your Pokus checkout:

    <!-- qa:run env=E1 -->
    ```bash
    cd <pokus-checkout>/infra
    ```

    `<pokus-checkout>` is the directory where you cloned the repository.

2. Run preflight without storing anything and save the JSON report:

    <!-- qa:run env=E1 -->
    ```bash
    ./preflight/pokus-preflight --no-write --output /tmp/host-profile.json
    echo "exit code: $?"
    ```

    The summary appears on the terminal and starts with the outcome, for example:

    ```text title="Output"
    Pokus preflight: DEGRADED (exit 10)
    ```

    Running it with `sudo` lets preflight read root-only details such as
    whether `/dev/kvm` opens read-write; `--no-write` still keeps it from
    storing the profile.

3. Read the verdict:

    | Exit code | Outcome | What to do |
    | --- | --- | --- |
    | 0 | `supported` | Nothing; the host is ready for bootstrap. |
    | 10 | `degraded` | Read *Disabled features*; decide whether the lab is still useful without them. |
    | 20 | `unsupported` | Read *Unsupported*; fix the cause or use a supported distribution in a VM. |
    | 30 | `needs_action` | Do what *Action needed* says, then run the check again. |
    | 40 | `preflight_error` | Detection failed; the traceback shows where. |

    The action `secure_boot_mok` (Secure Boot on, no NVIDIA driver) is the
    one exception: bootstrap can do it itself with your consent. Add
    `--accept-mok-enrollment` to see the verdict with that consent; the
    action then appears as a planned action among the *Warnings*
    ([Bare-metal hosts](../providers/baremetal.md#secure-boot-and-reboots)).

4. To see every detail, open the JSON report; the fields are described in the
   [host profile schema](../reference/host-profile.md).

## Verify the result

You should now see one outcome line and an exit code from the table above.
The JSON report contains the same `outcome`:

<!-- qa:run env=E1 -->
```bash
python3 -c 'import json; print(json.load(open("/tmp/host-profile.json"))["outcome"])'
```

The output is one of `supported`, `degraded`, `unsupported` or `needs_action`.
