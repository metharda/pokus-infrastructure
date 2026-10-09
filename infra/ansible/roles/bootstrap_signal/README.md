# bootstrap_signal

Stops the play with a condition that `bootstrap.sh` turns into its exit code
(BUILD_PROMPT §5.2 step 9): the role writes `/run/pinka/bootstrap-signal.json`
with `{code, message}` (and, for a 76, optionally `mok_key`) and fails. Roles call it instead of failing directly
whenever the operator has to act:

| Code | Meaning |
| --- | --- |
| 75 | WSL restart required (`wsl --terminate <distro>` on the Windows side) |
| 76 | Reboot required, or a MOK enrollment is pending; with `bootstrap_signal_mok_key`, bootstrap first imports that key (`mokutil --import`) with the operator's one-time password (approved deviation I1) |
| 77 | Another operator action; the message says which |

```yaml
- name: Ask for a reboot
  ansible.builtin.include_role:
    name: bootstrap_signal
  vars:
    bootstrap_signal_code: 76
    bootstrap_signal_message: "Reboot to load the NVIDIA driver, then re-run bootstrap."
```

## Variables

See `meta/argument_specs.yml`. The signal file is written even in check
mode, because bootstrap reads it.

## Idempotency

The role always fails by design; it changes nothing but the runtime signal file.
