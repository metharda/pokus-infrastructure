# bootstrap_signal

Stops the play with a condition that `bootstrap.sh` turns into its exit code
(BUILD_PROMPT §5.2 step 9): the role writes `/run/pokus/bootstrap-signal.json`
with `{code, message}` and fails. Roles call it instead of failing directly
whenever the operator has to act:

| Code | Meaning |
| --- | --- |
| 75 | WSL restart required (`wsl --terminate <distro>` on the Windows side) |
| 76 | Reboot required, or a MOK enrollment is pending |
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
