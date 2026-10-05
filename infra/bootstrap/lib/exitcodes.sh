# shellcheck shell=bash
# shellcheck disable=SC2034 # constants read by the scripts that source this file
# Managed by pokus-infra (bootstrap/lib/exitcodes.sh). Local changes will be overwritten.
#
# Exit codes of bootstrap.sh (BUILD_PROMPT §2.4, §5.2). One constant per line in
# the form `readonly EXIT_NAME=<code> # <title>: <meaning>`; tools/gen renders
# docs/reference/exit-codes.md from these lines and the docs coverage check
# requires every code to be documented.

readonly EXIT_OK=0               # success: bootstrap finished and doctor reports no warnings
readonly EXIT_DOCTOR_WARNINGS=1  # doctor warnings: bootstrap finished; doctor reports warnings only
readonly EXIT_DOCTOR_FAILURES=2  # doctor failures: bootstrap finished; doctor reports failed checks
readonly EXIT_DEGRADED=10        # degraded: preflight found optional features missing and there was no consent (--accept-degraded or interactive confirmation)
readonly EXIT_EXPERIMENTAL=11    # experimental: the host is on an experimental tier and there was no consent (--accept-experimental or interactive confirmation)
readonly EXIT_UNSUPPORTED=20     # unsupported: preflight or pre-preflight found the host unsupported and --force-unsupported was not given
readonly EXIT_NEEDS_ACTION=30    # needs action: an operator action is needed before bootstrap can continue; the remediation is printed
readonly EXIT_PREFLIGHT_ERROR=40 # preflight error: host detection failed unexpectedly; the traceback is in the log
readonly EXIT_TOOLING=50         # tooling failed: installing uv, CPython, the Ansible environment or the Ansible collections failed, or a checksum did not match
readonly EXIT_ANSIBLE=60         # play failed: an Ansible play failed without a bootstrap signal, or a later stage is not installed yet
readonly EXIT_USAGE=64           # usage error: invalid command-line options
readonly EXIT_INTERNAL=70        # internal error: unexpected error in bootstrap itself; the failing line is in the log
readonly EXIT_WSL_RESTART=75     # WSL restart required: run 'wsl --terminate <distro>' on the Windows side, then re-run bootstrap
readonly EXIT_REBOOT=76          # reboot required: reboot (and enroll the MOK if asked), then re-run bootstrap
readonly EXIT_OPERATOR=77        # operator action required: the printed message says what to do (for example run as root, commit the infra tree, accept a changed host profile)
