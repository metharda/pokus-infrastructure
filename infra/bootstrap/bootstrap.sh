#!/usr/bin/env bash
# Managed by pinka (bootstrap/bootstrap.sh). Local changes will be overwritten.
#
# Single entry point that provisions a Pokus lab host (BUILD_PROMPT §5):
#   sudo ./bootstrap/bootstrap.sh --machine <name> [options]
# Run with --help for the options; docs/reference/exit-codes.md lists the exit
# codes and docs/how-to/troubleshoot.md what to do about each. Re-running after
# any exit resumes cleanly: every stage is idempotent.
# shellcheck source-path=SCRIPTDIR
set -Eeuo pipefail
shopt -s inherit_errexit

BOOTSTRAP_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
INFRA_DIR=$(dirname -- "$BOOTSTRAP_DIR")
readonly BOOTSTRAP_DIR INFRA_DIR

# shellcheck source=lib/exitcodes.sh
source "$BOOTSTRAP_DIR/lib/exitcodes.sh"
# shellcheck source=lib/log.sh
source "$BOOTSTRAP_DIR/lib/log.sh"
# shellcheck source=lib/args.sh
source "$BOOTSTRAP_DIR/lib/args.sh"
# shellcheck source=lib/gitstate.sh
source "$BOOTSTRAP_DIR/lib/gitstate.sh"
# shellcheck source=lib/pins.sh
source "$BOOTSTRAP_DIR/lib/pins.sh"
# shellcheck source=lib/tooling.sh
source "$BOOTSTRAP_DIR/lib/tooling.sh"
# shellcheck source=lib/stages.sh
source "$BOOTSTRAP_DIR/lib/stages.sh"

# Exit codes that stages return on purpose; any other failure is internal.
readonly DELIBERATE_CODES=" $EXIT_DEGRADED $EXIT_EXPERIMENTAL $EXIT_UNSUPPORTED $EXIT_NEEDS_ACTION \
$EXIT_PREFLIGHT_ERROR $EXIT_TOOLING $EXIT_ANSIBLE $EXIT_USAGE $EXIT_INTERNAL $EXIT_WSL_RESTART \
$EXIT_REBOOT $EXIT_OPERATOR "

INTERACTIVE=false
USE_TUI=false

# exit_meaning CODE: the title and meaning of an exit code (from exitcodes.sh).
exit_meaning() {
  sed -n "s/^readonly EXIT_[A-Z_]*=$1 *# //p" "$BOOTSTRAP_DIR/lib/exitcodes.sh"
}

# on_err LINE COMMAND: ERR trap. Deliberate codes pass through; anything else
# is reported with its location and becomes EXIT_INTERNAL.
on_err() {
  local rc=$? line=$1 command=$2 where=${BASH_SOURCE[1]:-bootstrap.sh}
  trap - ERR
  if [[ $DELIBERATE_CODES != *" $rc "* ]]; then
    log_error "internal error: '$command' failed with exit status $rc (${where##*/}:$line)"
    rc=$EXIT_INTERNAL
  fi
  exit "$rc"
}

# on_exit: EXIT trap. Close the open stage, write the state file, report the
# result, and let the log writer finish.
on_exit() {
  local rc=$? i
  trap - ERR EXIT
  set +e
  if [[ -n $CURRENT_STAGE ]]; then
    stage_end "$CURRENT_STAGE" failed "$rc"
  fi
  if "$STATE_ENABLED"; then
    write_state "$rc" "$STATE_FILE" || log_warn "could not write $STATE_FILE"
  fi
  emit_event bootstrap.result bootstrap "$(result_name "$rc")" "$rc"
  if ((rc == 0)); then
    log_info "bootstrap finished: $(exit_meaning "$rc")"
  else
    log_error "bootstrap ended with exit code $rc: $(exit_meaning "$rc")"
    log_error "see docs/how-to/troubleshoot.md and the log $LOG_FILE"
  fi
  if [[ -n $TEE_PID ]]; then
    exec >&- 2>&-
    for ((i = 0; i < 50; i++)); do
      kill -0 "$TEE_PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
  exit "$rc"
}

main() {
  umask 022
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export PATH PYTHONUTF8=1
  trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR
  parse_args "$@" || exit "$?"
  if "$OPT_HELP"; then
    usage
    exit "$EXIT_OK"
  fi
  read_mok_password_stdin || exit "$?"
  stage_root
  # Decide about prompts before the output goes through the log writer.
  if is_interactive; then INTERACTIVE=true; fi
  if use_tui; then USE_TUI=true; fi
  stage_logging
  trap on_exit EXIT
  stage_git
  stage_prepreflight
  stage_tooling
  stage_frontend
  stage_preflight
  stage_ansible_env
  stage_ansible
  if "$OPT_RESTORE"; then
    stage_restore
  fi
  stage_doctor
  exit "$FINAL_RC"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
