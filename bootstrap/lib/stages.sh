# shellcheck shell=bash
# shellcheck disable=SC2034 # run-time state is read by the traps in bootstrap.sh
# Managed by pinka (bootstrap/lib/stages.sh). Local changes will be overwritten.
#
# The stages of bootstrap.sh (BUILD_PROMPT §5.2) and the decisions between
# them. A stage either returns normally or ends bootstrap with an exit code
# from exitcodes.sh: deliberate failures call `fail CODE MESSAGE`, library
# functions return their code and the ERR trap in bootstrap.sh passes it on.
# The decision helpers (gate_check, playbook_result, profile_change_accepted,
# state_json) touch nothing on the host and are unit-tested.
#
# Expects INFRA_DIR, BOOTSTRAP_DIR and INTERACTIVE to be set by bootstrap.sh.

readonly RUN_DIR=/run/pinka/bootstrap
readonly SIGNAL_FILE=/run/pinka/bootstrap-signal.json
readonly PROFILE_FILE=/etc/pinka/host-profile.json
readonly STATE_FILE=/etc/pinka/bootstrap-state.json
readonly STORED_VAULT_PASS=/etc/pinka/secrets/vault-pass
readonly LOG_DIR=/var/log/pinka-bootstrap
readonly LOCK_FILE=/run/lock/pinka-bootstrap.lock
readonly PINKA_LAB=/usr/local/bin/pinka
readonly SIGNAL_CODES=' 65 75 76 77 '
# Uninstall (§5.4): its working files, outside pinka's paths that it removes,
# and its reports, which it keeps.
readonly UNINSTALL_DIR=/run/pinka-uninstall
readonly UNINSTALL_REPORT_DIR=/var/log/pinka-uninstall
# The installer TUI runs from /opt/pinka: an uninstall it runs leaves the
# tooling to the bootstrap that started the TUI, through this file.
readonly TOOLING_HANDOFF=/run/pinka-uninstall-tooling

# Run-time state (read by the EXIT trap and the state file).
CURRENT_STAGE=''
LAST_STAGE=''
STARTED_AT=''
LOG_FILE=''
TEE_PID=''
STATE_ENABLED=false
SIGNAL_CODE=''
SIGNAL_MESSAGE=''
FINAL_RC=0
GRANTED_OVERRIDES=()
# The one-time MOK password (approved deviation I1): memory only, never
# exported, logged or written; cleared once mokutil has it.
MOK_PASSWORD=''

# fail CODE MESSAGE: log the message and end bootstrap with CODE.
fail() {
  log_error "$2"
  exit "$1"
}

# enter_stage NAME / leave_stage: track the current stage and emit events.
enter_stage() {
  CURRENT_STAGE=$1
  LAST_STAGE=$1
  stage_begin "$1"
}

leave_stage() {
  stage_end "$CURRENT_STAGE" ok
  CURRENT_STAGE=''
}

# pinka_python ARGS...: the pinned CPython (replaced in unit tests).
pinka_python() {
  "$PINKA_PYTHON" "$@"
}

# profile_tool ARGS...: python -m pinka_preflight.profile_tool with the pinned CPython.
profile_tool() {
  PYTHONPATH="$INFRA_DIR/preflight" pinka_python -m pinka_preflight.profile_tool "$@"
}

# confirm QUESTION: ask a yes/no question; false when prompting is not allowed.
confirm() {
  local answer=''
  "$INTERACTIVE" || return 1
  printf '%s [y/N] ' "$1" >&2
  read -r answer || return 1
  [[ $answer == [yY] || $answer == [yY][eE][sS] ]]
}

# gate_check OUTCOME TIER NEEDS_ACTION PROFILE: apply BUILD_PROMPT §2.4 to a
# preflight result. NEEDS_ACTION is the profile's needs_action list as JSON.
# Interactive consent is added to GRANTED_OVERRIDES. Returns 0 to continue,
# otherwise the bootstrap exit code (the reason is logged).
gate_check() {
  local outcome=$1 tier=$2 needs_action=$3 profile=$4
  GRANTED_OVERRIDES=()
  case $outcome in
    supported) ;;
    degraded)
      if ! "$OPT_ACCEPT_DEGRADED"; then
        profile_tool consent "$profile" degraded >&2 || return "$EXIT_INTERNAL"
        if ! confirm "Continue with these features disabled?"; then
          log_error "preflight: degraded; re-run with --accept-degraded to continue without these features"
          return "$EXIT_DEGRADED"
        fi
        GRANTED_OVERRIDES+=(accept_degraded)
      fi
      ;;
    unsupported)
      if ! "$OPT_FORCE_UNSUPPORTED"; then
        log_error "preflight: unsupported (reasons above); --force-unsupported continues and is recorded in every environment fingerprint"
        return "$EXIT_UNSUPPORTED"
      fi
      if [[ $needs_action != '[]' ]]; then
        log_error "preflight: operator action needed (remediation above); --force-unsupported does not skip it"
        return "$EXIT_NEEDS_ACTION"
      fi
      log_warn "continuing on an unsupported host (--force-unsupported); this is recorded in the host profile and every environment fingerprint"
      ;;
    needs_action)
      log_error "preflight: operator action needed; follow the remediation above, then re-run bootstrap"
      return "$EXIT_NEEDS_ACTION"
      ;;
    *)
      log_error "preflight returned an unknown outcome '$outcome'"
      return "$EXIT_PREFLIGHT_ERROR"
      ;;
  esac
  if [[ $tier == experimental ]] && ! "$OPT_ACCEPT_EXPERIMENTAL"; then
    profile_tool consent "$profile" experimental >&2 || return "$EXIT_INTERNAL"
    if ! confirm "Continue on the experimental tier?"; then
      log_error "preflight: experimental tier; re-run with --accept-experimental to continue"
      return "$EXIT_EXPERIMENTAL"
    fi
    GRANTED_OVERRIDES+=(accept_experimental)
  fi
  return 0
}

# state_result STATE_FILE: print the result recorded by the previous run.
state_result() {
  pinka_python -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("result", ""))
except (OSError, ValueError, AttributeError):
    print("")
' "$1"
}

# profile_change_accepted STATE_FILE: true if the host profile may change
# without --refresh-profile: the previous run ended with a bootstrap signal
# (WSL restart, reboot or operator action requested by the play), so the
# change is the expected result of that action (BUILD_PROMPT §5.2: re-runs
# after 75/76/77 resume cleanly).
profile_change_accepted() {
  [[ -f $1 && $(state_result "$1") == signal ]]
}

# read_signal FILE: print the signal code on the first line, the MOK key to
# import (or an empty line) on the second and the message after them; false if
# FILE is missing or not a valid signal.
read_signal() {
  [[ -f $1 ]] || return 1
  pinka_python -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
    code = int(data["code"])
    key = str(data.get("mok_key") or "")
    message = str(data.get("message", ""))
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    sys.exit(1)
if "\n" in key:
    sys.exit(1)
print(code)
print(key)
print(message)
' "$1"
}

# ask_mok_password: ask twice for a one-time MOK password (masked) and keep it
# in MOK_PASSWORD; false when prompting is not allowed or the answers differ.
ask_mok_password() {
  local first='' second=''
  "$INTERACTIVE" || return 1
  printf '%s' "One-time MOK password (you type it again at the blue MOK Manager screen): " >&2
  IFS= read -r -s first || return 1
  printf '\n%s' "The same password again: " >&2
  IFS= read -r -s second || return 1
  printf '\n' >&2
  if [[ -z $first || $first != "$second" ]]; then
    log_error "the MOK passwords are empty or differ"
    return 1
  fi
  MOK_PASSWORD=$first
}

# mok_password_ready: true if a one-time MOK password is at hand (from
# --mok-password-stdin, or asked for now on a terminal).
mok_password_ready() {
  [[ -n $MOK_PASSWORD ]] || ask_mok_password
}

# read_mok_password_stdin: with --mok-password-stdin, read the one-time MOK
# password from the first line of standard input (before anything else reads it).
read_mok_password_stdin() {
  "$OPT_MOK_PASSWORD_STDIN" || return 0
  if [[ -t 0 ]]; then
    usage_error "--mok-password-stdin reads a pipe; on a terminal bootstrap asks for the password itself"
    return
  fi
  IFS= read -r MOK_PASSWORD || true
  if [[ -z $MOK_PASSWORD ]]; then
    usage_error "--mok-password-stdin: no password on the first line of standard input"
    return
  fi
}

# require_mok_password PROFILE: when the profile plans the MOK enrollment
# (approved deviation I1), make sure the one-time password is at hand before
# the play installs the driver; EXIT_OPERATOR if it cannot be had.
require_mok_password() {
  local planned
  planned=$(profile_tool get "$1" planned_actions)
  [[ $planned == *'"secure_boot_mok"'* ]] || return 0
  "$OPT_CHECK" && return 0
  mok_password_ready && return 0
  log_error "the planned MOK enrollment needs a one-time password: pass it on standard input with --mok-password-stdin, or run bootstrap on a terminal"
  return "$EXIT_OPERATOR"
}

# import_mok KEY: import the module signing key for enrollment at the next
# boot (approved deviation I1). The one-time password reaches mokutil on its
# standard input only, never on a command line or on disk. Returns 0 when the
# key is imported (or in check mode), otherwise EXIT_OPERATOR with what to do.
import_mok() {
  local key=$1
  if "$OPT_CHECK"; then
    MOK_PASSWORD=''
    log_info "check mode: not importing $key"
    return 0
  fi
  if ! mok_password_ready; then
    log_error "Secure Boot: $key must be imported for enrollment and bootstrap has no one-time MOK password"
    log_error "import it yourself with 'sudo mokutil --import $key' (choose a one-time password), reboot, enroll it at the blue MOK Manager screen, then re-run bootstrap"
    return "$EXIT_OPERATOR"
  fi
  # TODO(verify) on reference hardware: mokutil --import reads the new password
  # twice from standard input when that is not a terminal.
  if ! printf '%s\n%s\n' "$MOK_PASSWORD" "$MOK_PASSWORD" | mokutil --import "$key"; then
    MOK_PASSWORD=''
    log_error "mokutil --import $key failed; run 'sudo mokutil --import $key' yourself, reboot, enroll the key, then re-run bootstrap"
    return "$EXIT_OPERATOR"
  fi
  MOK_PASSWORD=''
  log_info "imported $key: at the next boot choose Enroll MOK at the blue MOK Manager screen and enter the one-time password"
}

# schedule_reboot: after a reboot signal (76) with --allow-reboot, reboot in one
# minute, so that bootstrap still writes its state file and exits with 76
# (BUILD_PROMPT §3.2, §13.7). Never in check mode.
schedule_reboot() {
  if "$OPT_CHECK"; then
    log_info "check mode: not rebooting"
    return 0
  fi
  if shutdown -r +1 "pinka bootstrap: reboot required (--allow-reboot)"; then
    log_warn "the host reboots in one minute (--allow-reboot); run bootstrap again afterwards"
  else
    log_error "could not schedule the reboot; reboot the host yourself, then run bootstrap again"
  fi
}

# playbook_result RC SIGNAL_FILE: map an ansible-playbook exit status and the
# bootstrap signal file to a bootstrap exit code (BUILD_PROMPT §5.2 step 9).
# Sets SIGNAL_CODE and SIGNAL_MESSAGE for a valid signal.
playbook_result() {
  local rc=$1 file=$2 text code key
  if ((rc == 0)); then
    if [[ -f $file ]]; then
      log_warn "ignoring $file: the play succeeded"
    fi
    return 0
  fi
  if text=$(read_signal "$file"); then
    code=${text%%$'\n'*}
    text=${text#*$'\n'}
    key=${text%%$'\n'*}
    if [[ $SIGNAL_CODES == *" $code "* ]]; then
      SIGNAL_MESSAGE=${text#*$'\n'}
      log_error "$SIGNAL_MESSAGE"
      if ((code == EXIT_REBOOT)) && [[ -n $key ]]; then
        import_mok "$key" || code=$?
      fi
      SIGNAL_CODE=$code
      emit_event bootstrap.signal "$CURRENT_STAGE" signaled "$code" "$SIGNAL_MESSAGE"
      if ((code == EXIT_REBOOT)) && "$OPT_ALLOW_REBOOT"; then
        schedule_reboot
      fi
      return "$code"
    fi
    log_warn "ignoring $file: unknown signal code '$code'"
  fi
  log_error "ansible-playbook failed with exit status $rc; the failing task is above and in the log"
  return "$EXIT_ANSIBLE"
}

# json_bool VALUE: print true for "true", false otherwise.
json_bool() {
  if [[ $1 == true ]]; then printf 'true'; else printf 'false'; fi
}

# json_list CSV: print a JSON array of the comma-separated names (validated by args.sh).
json_list() {
  local item out='' sep=''
  local -a items=()
  [[ -n $1 ]] && IFS=, read -r -a items <<<"$1"
  for item in "${items[@]}"; do
    out+="$sep\"$(json_escape "$item")\""
    sep=','
  done
  printf '[%s]' "$out"
}

# override_granted NAME: true if the operator gave consent interactively.
override_granted() {
  local name
  for name in "${GRANTED_OVERRIDES[@]}"; do
    [[ $name == "$1" ]] && return 0
  done
  return 1
}

# effective_override NAME FLAG: print the override as a JSON boolean.
effective_override() {
  if [[ $2 == true ]] || override_granted "$1"; then printf 'true'; else printf 'false'; fi
}

# result_name RC: the result recorded in the state file.
result_name() {
  if [[ -n $SIGNAL_CODE ]]; then
    printf 'signal'
    return
  fi
  case $1 in
    0) printf 'success' ;;
    1) printf 'doctor_warnings' ;;
    2) printf 'doctor_failures' ;;
    *) printf 'failed' ;;
  esac
}

# state_json RC FINISHED_AT: print /etc/pinka/bootstrap-state.json (BUILD_PROMPT §5.2 step 12).
state_json() {
  local rc=$1 finished=$2 signal=null
  if [[ -n $SIGNAL_CODE ]]; then
    signal=$(printf '{"code": %d, "message": "%s"}' "$SIGNAL_CODE" "$(json_escape "$SIGNAL_MESSAGE")")
  fi
  cat <<EOF
{
  "schema_version": 1,
  "machine": "$(json_escape "$OPT_MACHINE")",
  "repo_commit": "$(json_escape "$GIT_COMMIT")",
  "infra_tree": "$(json_escape "$GIT_INFRA_TREE")",
  "infra_dirty": $(json_bool "$GIT_INFRA_DIRTY"),
  "infra_source": "$(json_escape "$GIT_SOURCE")",
  "infra_dir": "$(json_escape "$INFRA_DIR")",
  "release_tag": "$(json_escape "$RELEASE_TAG")",
  "ref": "$(json_escape "${OPT_REF:-HEAD}")",
  "started_at": "$(json_escape "$STARTED_AT")",
  "finished_at": "$(json_escape "$finished")",
  "tags": $(json_list "$OPT_TAGS"),
  "skip_tags": $(json_list "$OPT_SKIP_TAGS"),
  "restore": $(json_bool "$OPT_RESTORE"),
  "overrides": {
    "accept_degraded": $(effective_override accept_degraded "$OPT_ACCEPT_DEGRADED"),
    "accept_experimental": $(effective_override accept_experimental "$OPT_ACCEPT_EXPERIMENTAL"),
    "force_unsupported": $(json_bool "$OPT_FORCE_UNSUPPORTED"),
    "adopt_incus": $(json_bool "$OPT_ADOPT_INCUS"),
    "accept_mok_enrollment": $(json_bool "$OPT_ACCEPT_MOK_ENROLLMENT"),
    "replace_platform": "$(json_escape "$OPT_REPLACE_PLATFORM")",
    "allow_dirty": $(json_bool "$OPT_ALLOW_DIRTY"),
    "allow_community_repos": $(json_bool "$OPT_ALLOW_COMMUNITY_REPOS"),
    "refresh_profile": $(json_bool "$OPT_REFRESH_PROFILE")
  },
  "stage": "$(json_escape "$LAST_STAGE")",
  "result": "$(result_name "$rc")",
  "exit_code": $rc,
  "signal": $signal,
  "log_file": "$(json_escape "$LOG_FILE")"
}
EOF
}

# write_state RC FILE: write the state file atomically (0644 root).
write_state() {
  local rc=$1 file=$2 dir tmp
  dir=$(dirname -- "$file")
  install -d -m 0755 "$dir"
  tmp=$(mktemp "$dir/.bootstrap-state.XXXXXX")
  state_json "$rc" "$(utc_now)" >"$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$file"
}

# extra_vars_json PROFILE: print the variables bootstrap passes to Ansible:
# the host profile under host_profile and the bootstrap options.
extra_vars_json() {
  local profile=$1
  printf '{"host_profile": %s,\n' "$(cat -- "$profile")"
  cat <<EOF
 "pinka_bootstrap": {
  "machine": "$(json_escape "$OPT_MACHINE")",
  "repo_commit": "$(json_escape "$GIT_COMMIT")",
  "infra_tree": "$(json_escape "$GIT_INFRA_TREE")",
  "infra_dirty": $(json_bool "$GIT_INFRA_DIRTY"),
  "ref": "$(json_escape "${OPT_REF:-HEAD}")",
  "check": $(json_bool "$OPT_CHECK"),
  "refresh_profile": $(json_bool "$OPT_REFRESH_PROFILE"),
  "allow_reboot": $(json_bool "$OPT_ALLOW_REBOOT"),
  "adopt_incus": $(json_bool "$OPT_ADOPT_INCUS"),
  "inspect_platform": $(json_bool "$OPT_INSPECT_PLATFORM"),
  "replace_platform": "$(json_escape "$OPT_REPLACE_PLATFORM")",
  "allow_community_repos": $(json_bool "$OPT_ALLOW_COMMUNITY_REPOS"),
  "offline": $(json_bool "$OPT_OFFLINE"),
  "interactive": $(json_bool "$INTERACTIVE"),
  "profile_path": "$(json_escape "$profile")",
  "signal_file": "$(json_escape "$SIGNAL_FILE")"
 }
}
EOF
}

# ---------------------------------------------------------------------------
# Stages (called in order by bootstrap.sh)
# ---------------------------------------------------------------------------

# stage_root: bootstrap changes the host, so it needs root (§5.2 step 1).
stage_root() {
  if [[ $(id -u) != 0 ]]; then
    log_error "bootstrap must run as root; re-run it with sudo:"
    log_error "  sudo $0 --machine ${OPT_MACHINE:-<name>} [options]"
    exit "$EXIT_OPERATOR"
  fi
}

# stage_logging: tee all output to /var/log/pinka-bootstrap/<UTC>.log (0600)
# and take the bootstrap lock.
stage_logging() {
  STARTED_AT=$(utc_now)
  install -d -m 0700 "$LOG_DIR"
  LOG_FILE=$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ).log
  (umask 077 && : >>"$LOG_FILE")
  if "$OPT_JSON"; then
    # --json: standard output carries only the JSON (on descriptor 8); the
    # log goes to standard error.
    exec 8>&1
    exec > >(tee -a -- "$LOG_FILE" >&2) 2>&1
  else
    exec > >(tee -a -- "$LOG_FILE") 2>&1
  fi
  TEE_PID=$!
  install -d -m 0755 "$(dirname -- "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    fail "$EXIT_OPERATOR" "another bootstrap is running on this host; wait for it to finish"
  fi
  log_info "bootstrap started; log: $LOG_FILE"
}

# stage_git: record the checkout or the release package (owner's choice in
# 3d.8) and refuse a dirty tree (§5.2 step 1).
stage_git() {
  local rc=0
  enter_stage git
  command -v git >/dev/null 2>&1 \
    || fail "$EXIT_NEEDS_ACTION" "git is not installed; install it with the distribution's package manager"
  git_state "$INFRA_DIR" || rc=$?
  if ((rc == 1)); then
    rc=0
    package_state "$INFRA_DIR" || rc=$?
    case $rc in
      0) ;;
      1) fail "$EXIT_OPERATOR" "bootstrap must run from a git checkout of the pokus-infrastructure repository or from an unpacked release package" ;;
      2) fail "$EXIT_OPERATOR" "$INFRA_DIR must be owned by root or by the user running sudo" ;;
      3) fail "$EXIT_OPERATOR" "release/RELEASE.json of this package names no commit or tree; unpack the release package again" ;;
      *) fail "$EXIT_TOOLING" "git could not hash the files of this release package" ;;
    esac
  else
    case $rc in
      0) ;;
      2) fail "$EXIT_OPERATOR" "$GIT_REPO_ROOT must be owned by root or by the user running sudo" ;;
      *) fail "$EXIT_OPERATOR" "bootstrap must run from a git checkout of the pokus-infrastructure repository" ;;
    esac
  fi
  [[ -n $GIT_COMMIT && -n $GIT_INFRA_TREE ]] \
    || fail "$EXIT_OPERATOR" "cannot read the checked-out commit of $GIT_REPO_ROOT"
  STATE_ENABLED=true
  # Check mode provisions nothing, and uninstall removes /etc/pinka.
  if "$OPT_CHECK" || "$OPT_UNINSTALL"; then STATE_ENABLED=false; fi
  if [[ $GIT_SOURCE == package ]]; then
    if [[ -n $OPT_REF ]] && ! ref_matches_package "$OPT_REF"; then
      fail "$EXIT_OPERATOR" "--ref $OPT_REF is not this package's release (${RELEASE_TAG:-no tag}, commit $GIT_COMMIT); unpack that release's package"
    fi
    if "$GIT_INFRA_DIRTY"; then
      "$OPT_ALLOW_DIRTY" || fail "$EXIT_OPERATOR" \
        "the package's files differ from its release (tree $PACKAGE_TREE, release/RELEASE.json records $GIT_INFRA_TREE); unpack it again, or re-run with --allow-dirty (recorded)"
      log_warn "the package's files differ from its release (--allow-dirty); this is recorded"
    fi
    log_info "release package ${RELEASE_TAG:-(no tag)}: repository commit $GIT_COMMIT, tree $GIT_INFRA_TREE"
    leave_stage
    return 0
  fi
  if [[ -n $OPT_REF ]] && ! ref_matches_head "$OPT_REF"; then
    fail "$EXIT_OPERATOR" "--ref $OPT_REF is not the checked-out commit $GIT_COMMIT; check it out first"
  fi
  if "$GIT_INFRA_DIRTY"; then
    "$OPT_ALLOW_DIRTY" || fail "$EXIT_OPERATOR" \
      "the repository has uncommitted changes; commit them, or re-run with --allow-dirty (recorded)"
    log_warn "the repository has uncommitted changes (--allow-dirty); this is recorded"
  fi
  log_info "repository commit $GIT_COMMIT, tree $GIT_INFRA_TREE"
  leave_stage
}

# stage_prepreflight: minimal sanity checks in POSIX sh (§5.2 step 2).
stage_prepreflight() {
  local rc=0 mode=--non-interactive
  enter_stage prepreflight
  "$INTERACTIVE" && mode=--interactive
  sh "$BOOTSTRAP_DIR/lib/prepreflight.sh" "$mode" || rc=$?
  case $rc in
    0) ;;
    "$EXIT_UNSUPPORTED" | "$EXIT_NEEDS_ACTION" | "$EXIT_WSL_RESTART" | "$EXIT_OPERATOR") exit "$rc" ;;
    *) fail "$EXIT_INTERNAL" "prepreflight.sh failed with exit status $rc" ;;
  esac
  leave_stage
}

# stage_tooling: pinned uv and CPython under /opt/pinka (§5.2 steps 2 and 4, §19.3).
stage_tooling() {
  enter_stage tooling
  install -d -m 0755 "$PINKA_OPT"
  install_uv
  install_python
  leave_stage
}

# installer ARGS...: the installer package with its hash-locked environment.
installer() {
  PYTHONPATH="$INFRA_DIR/installer:$INFRA_DIR/tools/platform" "$INSTALLER_VENV/bin/python" -m pinka_installer \
    --infra "$INFRA_DIR" "$@"
}

# answers_field NAME TEXT: the value of NAME in installer describe's key=value lines.
answers_field() {
  sed -n "s/^$1=//p" <<<"$2"
}

# apply_answers: replay an answers file (§18.6): take the machine name, the
# mode and the snapshot from it, and write host_vars, the researchers, the
# vault and the answers file before preflight runs.
apply_answers() {
  local described machine mode password=$OPT_VAULT_PASSWORD_FILE
  described=$(installer describe --answers "$OPT_ANSWERS") \
    || fail "$EXIT_OPERATOR" "the answers file $OPT_ANSWERS cannot be used (problems above)"
  machine=$(answers_field machine "$described")
  mode=$(answers_field mode "$described")
  if [[ -n $OPT_MACHINE && $OPT_MACHINE != "$machine" ]]; then
    fail "$EXIT_USAGE" "--machine $OPT_MACHINE does not match the answers file (machine $machine)"
  fi
  OPT_MACHINE=$machine
  [[ $mode == restore ]] && OPT_RESTORE=true
  [[ $(answers_field accept_mok_enrollment "$described") == true ]] && OPT_ACCEPT_MOK_ENROLLMENT=true
  if [[ -z $password && -f $STORED_VAULT_PASS ]]; then
    password=$STORED_VAULT_PASS
  fi
  [[ -n $password ]] || fail "$EXIT_OPERATOR" \
    "--answers needs --vault-password-file: the vault is written with that password"
  local replace=()
  [[ -n $OPT_REPLACE_PLATFORM ]] && replace=(--replace-platform "$OPT_REPLACE_PLATFORM")
  installer apply --answers "$OPT_ANSWERS" --machine "$machine" --vault-password-file "$password" \
    "${replace[@]}" || fail "$EXIT_OPERATOR" "the answers could not be applied (reason above)"
  log_info "applied the answers for $machine (mode $mode)"
}

# tui_bootstrap_args: print the options the TUI passes on to the bootstrap it
# runs, one per line (the operator's overrides; the rest it decides itself).
tui_bootstrap_args() {
  local flag
  for flag in ACCEPT_DEGRADED:--accept-degraded ACCEPT_EXPERIMENTAL:--accept-experimental \
    FORCE_UNSUPPORTED:--force-unsupported REFRESH_PROFILE:--refresh-profile \
    ALLOW_REBOOT:--allow-reboot ALLOW_DIRTY:--allow-dirty ADOPT_INCUS:--adopt-incus \
    ACCEPT_MOK_ENROLLMENT:--accept-mok-enrollment \
    ALLOW_COMMUNITY_REPOS:--allow-community-repos OFFLINE:--offline CHECK:--check; do
    local name=OPT_${flag%%:*}
    if "${!name}"; then
      printf '%s\n' "${flag#*:}"
    fi
  done
  [[ -n $OPT_TAGS ]] && printf '%s\n' "--tags=$OPT_TAGS"
  [[ -n $OPT_SKIP_TAGS ]] && printf '%s\n' "--skip-tags=$OPT_SKIP_TAGS"
  [[ -n $OPT_REPLACE_PLATFORM ]] && printf '%s\n' "--replace-platform=$OPT_REPLACE_PLATFORM"
  return 0
}

# run_tui: hand the terminal to the installer TUI (§5.3, §18). The lock is
# released and the output no longer goes through the log writer: the TUI runs
# bootstrap again, non-interactively, for the installation itself, and that
# run writes its own log, state file and events. When the TUI ends, the
# /opt/pinka an uninstall it ran left behind (the TUI runs from it) goes.
run_tui() {
  local arg rc=0
  local -a args=()
  [[ -n $OPT_MACHINE ]] && args+=(--machine "$OPT_MACHINE")
  [[ -n $OPT_ANSWERS ]] && args+=(--answers "$OPT_ANSWERS")
  while IFS= read -r arg; do
    [[ -n $arg ]] && args+=("--bootstrap-arg=$arg")
  done < <(tui_bootstrap_args)
  log_info "starting the installer TUI (this run's log: $LOG_FILE)"
  leave_stage
  STATE_ENABLED=false
  trap - ERR EXIT
  flock -u 9
  exec 9>&- </dev/tty >/dev/tty 2>&1
  rm -f -- "$TOOLING_HANDOFF"
  env PYTHONPATH="$INFRA_DIR/installer:$INFRA_DIR/tools/platform" PINKA_BOOTSTRAP_LOG="$LOG_FILE" \
    PINKA_TOOLING_HANDOFF="$TOOLING_HANDOFF" "$INSTALLER_VENV/bin/python" -m pinka_installer \
    --infra "$INFRA_DIR" tui "${args[@]}" || rc=$?
  finish_tooling_handoff "$TOOLING_HANDOFF" "$PINKA_OPT" || rc=$EXIT_UNINSTALL_KEPT
  exit "$rc"
}

# finish_tooling_handoff FILE DIR: after the TUI ended, remove DIR (/opt/pinka)
# when an uninstall the TUI ran left it (FILE names that uninstall's report),
# and close the report's pending item (§5.4). False when DIR stays.
finish_tooling_handoff() {
  [[ -f $1 ]] || return 0
  UNINSTALL_REPORT=$(head -n 1 -- "$1")
  rm -f -- "$1"
  remove_tooling "$2"
  [[ ! -e $2 ]]
}

# terminal_problem: print why this terminal cannot show the TUI, or nothing.
terminal_problem() {
  local size rows cols
  if [[ ${TERM:-dumb} == dumb ]]; then
    printf 'TERM is %s' "${TERM:-unset}"
    return
  fi
  size=$(stty size 2>/dev/null </dev/tty) || {
    printf 'no terminal size'
    return
  }
  read -r rows cols <<<"$size"
  if ((rows < 24 || cols < 80)); then
    printf 'the terminal is %sx%s, the installer needs 80x24' "$cols" "$rows"
  fi
}

# stage_frontend: the installer (§5.3, §18): replay an answers file, or hand
# over to the TUI on a suitable terminal; otherwise continue without it.
stage_frontend() {
  local problem
  enter_stage frontend
  if [[ -n $OPT_ANSWERS ]] || "$USE_TUI"; then
    setup_venv "$INSTALLER_VENV" "$INFRA_DIR/installer/requirements.lock"
  fi
  if "$USE_TUI"; then
    problem=$(terminal_problem)
    if [[ -n $problem ]]; then
      log_error "the installer TUI cannot start: $problem"
      fail "$EXIT_OPERATOR" "use a larger terminal, or answers: --answers installer-answers/<machine>.yml (docs/how-to/install-without-the-tui.md)"
    fi
    run_tui
  fi
  if [[ -n $OPT_ANSWERS ]]; then
    apply_answers
  fi
  [[ -n $OPT_MACHINE ]] || fail "$EXIT_USAGE" "--machine is required"
  leave_stage
}

# yaml_scalar FILE KEY: print the plain value of the top-level KEY of a YAML
# file (quotes and a trailing comment removed); nothing if FILE or KEY is missing.
yaml_scalar() {
  [[ -f $1 ]] || return 0
  sed -n -E "s/^$2:[[:space:]]*[\"']?([^\"'#[:space:]]*)[\"']?[[:space:]]*(#.*)?\$/\\1/p" -- "$1" | tail -n 1
}

# nvidia_driver_mode: the machine's install_nvidia_driver (host_vars, else
# group_vars/all/main.yml, else auto); fails unless auto, manual or skip.
nvidia_driver_mode() {
  local mode
  mode=$(yaml_scalar "$INFRA_DIR/ansible/inventory/host_vars/$OPT_MACHINE.yml" install_nvidia_driver)
  [[ -n $mode ]] || mode=$(yaml_scalar "$INFRA_DIR/ansible/group_vars/all/main.yml" install_nvidia_driver)
  mode=${mode:-auto}
  case $mode in
    auto | manual | skip) printf '%s' "$mode" ;;
    *)
      log_error "install_nvidia_driver is '$mode'; it must be auto, manual or skip"
      return "$EXIT_OPERATOR"
      ;;
  esac
}

# port_overrides FILE: print NAME=PORT for every entry of the top-level
# `ports:` mapping of FILE, written as a block (`ports:`, then indented
# `name: 18750` lines) or on one line (`ports: {name: 18750, other: 18751}`);
# nothing if FILE or the key is missing. Returns 1 for any other form.
port_overrides() {
  [[ -f $1 ]] || return 0
  awk '
    function emit(entry, kv) {
      sub(/^[ \t]+/, "", entry)
      sub(/[ \t]+$/, "", entry)
      if (entry == "") return
      if (entry !~ /^[a-z_]+[ \t]*:[ \t]*[0-9]+$/) { bad = 1; return }
      split(entry, kv, ":")
      gsub(/[ \t]/, "", kv[1])
      gsub(/[ \t]/, "", kv[2])
      print kv[1] "=" kv[2]
    }
    function strip(line) {
      sub(/[ \t]+#.*$/, "", line)
      return line
    }
    /^ports:/ {
      line = strip($0)
      sub(/^ports:[ \t]*/, "", line)
      if (line == "") { block = 1; next }
      if (line !~ /^[{].*[}]$/) { bad = 1; next }
      sub(/^[{]/, "", line)
      sub(/[}]$/, "", line)
      n = split(line, parts, ",")
      for (i = 1; i <= n; i++) emit(parts[i])
      next
    }
    block && /^[ \t]+[^ \t#]/ { emit(strip($0)); next }
    block && /^[^ \t#]/ { block = 0 }
    END { exit bad }
  ' "$1"
}

# researcher_names FILE: print the name of every item of the top-level
# `researchers:` list of FILE, written as flow mappings (`- {name: x, ...}`)
# or block mappings (`- name: x`); nothing if FILE or the key is missing.
researcher_names() {
  [[ -f $1 ]] || return 0
  awk '
    function clean(value) {
      sub(/[ \t]+#.*$/, "", value)
      gsub(/["\047 \t]/, "", value)
      if (value != "") print value
    }
    /^researchers:/ { block = 1; next }
    block && /^[^ \t#]/ { block = 0 }
    block && /^[ \t]*-[ \t]*[{]/ {
      if (match($0, /[{,][ \t]*name:[ \t]*[^,} \t]+/)) {
        value = substr($0, RSTART, RLENGTH)
        sub(/^[{,][ \t]*name:[ \t]*/, "", value)
        clean(value)
      }
      next
    }
    block && /^[ \t]*(-[ \t]+)?name:/ {
      value = $0
      sub(/^[ \t]*(-[ \t]+)?name:[ \t]*/, "", value)
      clean(value)
    }
  ' "$1"
}

# lab_preflight_args: the preflight options of the machine's port overrides,
# VM collector address and pool device in host_vars, and of the configured
# researchers (host_vars when it lists them, else group_vars), one per line
# (§2.2, §7.7, §12.11).
lab_preflight_args() {
  local host_vars="$INFRA_DIR/ansible/inventory/host_vars/$OPT_MACHINE.yml" overrides entry address
  local source device names
  overrides=$(port_overrides "$host_vars") || {
    log_error "ports: in $host_vars must map port names to numbers, as indented lines or as {name: number}"
    return "$EXIT_OPERATOR"
  }
  while IFS= read -r entry; do
    [[ -z $entry ]] || printf -- '--port=%s\n' "$entry"
  done <<<"$overrides"
  address=$(yaml_scalar "$host_vars" vm_collector_address)
  [[ -z $address ]] || printf -- '--vm-collector-address=%s\n' "$address"
  source=$(yaml_scalar "$host_vars" incus_pool_source)
  device=$(yaml_scalar "$host_vars" incus_pool_device)
  [[ $source != device || -z $device ]] || printf -- '--pool-device=%s\n' "$device"
  if [[ -f $host_vars ]] && grep -q '^researchers:' "$host_vars"; then
    names=$(researcher_names "$host_vars")
  else
    names=$(researcher_names "$INFRA_DIR/ansible/group_vars/all/main.yml")
  fi
  while IFS= read -r entry; do
    [[ -z $entry ]] || printf -- '--researcher=%s\n' "$entry"
  done <<<"$names"
}

# stage_preflight: run preflight, apply the gate, check for a changed host
# profile and store the profile (§2.4, §2.6, §5.2 step 3).
stage_preflight() {
  local rc=0 outcome tier needs_action changes mode lab
  local -a args=(--no-write --output "$RUN_DIR/host-profile.json" --repo-commit "$GIT_COMMIT")
  local -a lab_args=()
  enter_stage preflight
  install -d -m 0700 "$RUN_DIR"
  mode=$(nvidia_driver_mode) || exit "$EXIT_OPERATOR"
  args+=(--nvidia-driver "$mode")
  lab=$(lab_preflight_args) || exit "$EXIT_OPERATOR"
  [[ -z $lab ]] || mapfile -t lab_args <<<"$lab"
  args+=("${lab_args[@]}")
  "$OPT_ACCEPT_MOK_ENROLLMENT" && args+=(--accept-mok-enrollment)
  "$OPT_ACCEPT_DEGRADED" && args+=(--accept-degraded)
  "$OPT_ACCEPT_EXPERIMENTAL" && args+=(--accept-experimental)
  "$OPT_FORCE_UNSUPPORTED" && args+=(--force-unsupported)
  "$OPT_ADOPT_INCUS" && args+=(--adopt-incus)
  "$OPT_ALLOW_DIRTY" && args+=(--allow-dirty)
  "$OPT_OFFLINE" && args+=(--offline)
  "$INFRA_DIR/preflight/pinka-preflight" "${args[@]}" || rc=$?
  case $rc in
    0 | 10 | 20 | 30) ;;
    *) fail "$EXIT_PREFLIGHT_ERROR" "preflight failed (exit status $rc); the traceback is above and in the log" ;;
  esac
  outcome=$(profile_tool get "$RUN_DIR/host-profile.json" outcome)
  tier=$(profile_tool get "$RUN_DIR/host-profile.json" tier)
  needs_action=$(profile_tool get "$RUN_DIR/host-profile.json" needs_action)
  rc=0
  gate_check "$outcome" "$tier" "$needs_action" "$RUN_DIR/host-profile.json" || rc=$?
  ((rc == 0)) || exit "$rc"
  if ((${#GRANTED_OVERRIDES[@]} > 0)); then
    profile_tool override "$RUN_DIR/host-profile.json" "${GRANTED_OVERRIDES[@]}"
  fi
  require_mok_password "$RUN_DIR/host-profile.json" || exit "$?"
  if [[ -f $PROFILE_FILE ]] && ! "$OPT_REFRESH_PROFILE"; then
    rc=0
    changes=$(profile_tool diff "$PROFILE_FILE" "$RUN_DIR/host-profile.json") || rc=$?
    case $rc in
      0) ;;
      1)
        log_warn "the host profile changed since the last bootstrap:"
        log_warn "${changes//$'\n'/; }"
        profile_change_accepted "$STATE_FILE" \
          || fail "$EXIT_OPERATOR" "check these changes, then re-run with --refresh-profile to accept them"
        log_info "accepted: the last run ended with a restart, reboot or operator action"
        ;;
      *) fail "$EXIT_OPERATOR" "cannot read $PROFILE_FILE; fix it or re-run with --refresh-profile" ;;
    esac
  fi
  if "$OPT_CHECK"; then
    log_info "check mode: the host profile is not stored"
  else
    profile_tool store "$RUN_DIR/host-profile.json"
  fi
  leave_stage
}

# stage_ansible_env: the hash-locked Ansible environment, the pinned
# collections, and the versions.yml sync check (§5.2 steps 5-7).
stage_ansible_env() {
  enter_stage ansible-env
  setup_venv "$ANSIBLE_VENV" "$BOOTSTRAP_DIR/requirements.lock"
  install_collections "$ANSIBLE_COLLECTIONS" || fail "$EXIT_TOOLING" "the pinned Ansible collections could not be installed"
  check_versions_sync "$INFRA_DIR/versions.lock.yml" "$INFRA_DIR/ansible/group_vars/all/versions.yml" \
    || fail "$EXIT_OPERATOR" "ansible/group_vars/all/versions.yml does not match versions.lock.yml"
  leave_stage
}

# vault_args: print the ansible-playbook options for the vault password, one
# per line; false if a password is needed but cannot be asked for.
vault_args() {
  if [[ -n $OPT_VAULT_PASSWORD_FILE ]]; then
    printf '%s\n' --vault-password-file "$OPT_VAULT_PASSWORD_FILE"
  elif [[ -f $STORED_VAULT_PASS ]]; then
    printf '%s\n' --vault-password-file "$STORED_VAULT_PASS"
  elif "$INTERACTIVE"; then
    printf '%s\n' --ask-vault-pass
  else
    return 1
  fi
}

# run_playbook PLAYBOOK: run one playbook and map its result (§5.2 steps 8-9).
run_playbook() {
  local playbook=$1 rc=0 vars=$RUN_DIR/extra-vars.json profile=$RUN_DIR/host-profile.json text
  local -a vault=() cmd=()
  [[ -f $INFRA_DIR/ansible/inventory/host_vars/$OPT_MACHINE.yml ]] || fail "$EXIT_OPERATOR" \
    "ansible/inventory/host_vars/$OPT_MACHINE.yml does not exist; create it from example-machine.yml"
  if [[ -f $INFRA_DIR/ansible/group_vars/all/vault.yml ]]; then
    text=$(vault_args) || fail "$EXIT_OPERATOR" "the vault needs a password: pass --vault-password-file"
    mapfile -t vault <<<"$text"
  fi
  (umask 077 && extra_vars_json "$profile" >"$vars")
  cmd=("$ANSIBLE_VENV/bin/ansible-playbook" -i "$INFRA_DIR/ansible/inventory/hosts.yml"
    "$INFRA_DIR/ansible/$playbook" -e "machine=$OPT_MACHINE" -e "@$vars" "${vault[@]}")
  [[ -n $OPT_TAGS ]] && cmd+=(--tags "$OPT_TAGS")
  [[ -n $OPT_SKIP_TAGS ]] && cmd+=(--skip-tags "$OPT_SKIP_TAGS")
  "$OPT_CHECK" && cmd+=(--check)
  install -d -m 0755 "$(dirname -- "$SIGNAL_FILE")"
  rm -f -- "$SIGNAL_FILE"
  log_info "running: ${cmd[*]}"
  (cd -- "$INFRA_DIR/ansible" \
    && ANSIBLE_CONFIG=$INFRA_DIR/ansible/ansible.cfg LC_ALL=C.UTF-8 LANG=C.UTF-8 \
      PYTHONDONTWRITEBYTECODE=1 "${cmd[@]}") || rc=$?
  playbook_result "$rc" "$SIGNAL_FILE" || exit "$?"
}

# stage_ansible: the provisioning play (§5.2 step 8).
stage_ansible() {
  enter_stage ansible
  run_playbook site.yml
  leave_stage
}

# stage_inspect_platform: --inspect-platform runs only the platform role, which
# fetches, stages and validates the release, prints its summary and ends the
# play (§7.14 step 4). No bootstrap state is written: nothing was provisioned.
stage_inspect_platform() {
  enter_stage inspect-platform
  STATE_ENABLED=false
  OPT_TAGS=platform
  run_playbook site.yml
  FINAL_RC=0
  leave_stage
}

# stage_restore: restore.yml after provisioning (§5.2 step 10).
stage_restore() {
  enter_stage restore
  run_playbook restore.yml
  leave_stage
}

# stage_doctor: run doctor and use its exit code (§5.2 step 11).
stage_doctor() {
  local rc=0
  enter_stage doctor
  if "$OPT_CHECK"; then
    log_info "check mode: doctor is skipped"
    FINAL_RC=0
    leave_stage
    return 0
  fi
  [[ -x $PINKA_LAB ]] || fail "$EXIT_ANSIBLE" "$PINKA_LAB is not installed; the play has not provisioned the doctor yet"
  "$PINKA_LAB" doctor || rc=$?
  case $rc in
    0 | 1 | 2) FINAL_RC=$rc ;;
    *)
      log_error "doctor ended with exit status $rc; counted as failures"
      FINAL_RC=$EXIT_DOCTOR_FAILURES
      ;;
  esac
  leave_stage
}

# ---------------------------------------------------------------------------
# Uninstall (§5.4): bootstrap.sh --uninstall
# ---------------------------------------------------------------------------

# Whether /opt/pinka existed when this run began: a dry run removes the
# tooling it had to install for itself, so it leaves the host as it was.
OPT_EXISTED=false
UNINSTALL_MACHINE=''
UNINSTALL_REPORT=''
# The report's one item the play leaves open: /opt/pinka, which bootstrap
# removes after the play that runs from it.
readonly PENDING_ITEM='"result": "pending"'
readonly PENDING_REASON='bootstrap removes it after the play'

# json_field FILE KEY: print a top-level value of a JSON object file, a
# string as it is and anything else as JSON; nothing when it is missing.
json_field() {
  pinka_python -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle).get(sys.argv[2])
except (OSError, ValueError, AttributeError):
    sys.exit(0)
if value is not None:
    print(value if isinstance(value, str) else json.dumps(value))
' "$1" "$2"
}

# confirm_yes QUESTION: ask a yes/no question whose default is yes; false
# when prompting is not allowed or the answer is no.
confirm_yes() {
  local answer=''
  "$INTERACTIVE" || return 1
  printf '%s [Y/n] ' "$1" >&2
  read -r answer || return 1
  [[ -z $answer || $answer == [yY] || $answer == [yY][eE][sS] ]]
}

# ask_typed PROMPT: print the line the operator types; false when prompting
# is not allowed.
ask_typed() {
  local answer=''
  "$INTERACTIVE" || return 1
  printf '%s ' "$1" >&2
  read -r answer || return 1
  printf '%s' "$answer"
}

# uninstall_machine STATE_FILE: set UNINSTALL_MACHINE to the machine name the
# install recorded in the state file; --machine, when given, must name the
# same. Without a state file, --machine names it. Returns
# EXIT_UNINSTALL_REFUSED when they differ.
uninstall_machine() {
  local recorded=''
  [[ -f $1 ]] && recorded=$(json_field "$1" machine)
  if [[ -n $recorded && -n $OPT_MACHINE && $OPT_MACHINE != "$recorded" ]]; then
    log_error "--machine $OPT_MACHINE is not this host's machine: $1 names $recorded"
    return "$EXIT_UNINSTALL_REFUSED"
  fi
  UNINSTALL_MACHINE=${recorded:-$OPT_MACHINE}
}

# uninstall_confirmed: true when the operator confirmed the removal with the
# machine name: typed on the terminal (twice with --purge-data, the second
# time for the lab's data) or given with --confirm-machine.
uninstall_confirmed() {
  local typed
  if [[ -n $OPT_CONFIRM_MACHINE ]]; then
    if [[ $OPT_CONFIRM_MACHINE != "$UNINSTALL_MACHINE" ]]; then
      log_error "--confirm-machine $OPT_CONFIRM_MACHINE is not this host's machine name"
      return 1
    fi
    return 0
  fi
  if ! "$INTERACTIVE"; then
    log_error "without a terminal, uninstall needs the machine name: --confirm-machine NAME"
    return 1
  fi
  typed=$(ask_typed "Type the machine name ($UNINSTALL_MACHINE) to remove pinka from this host:") || return 1
  if [[ $typed != "$UNINSTALL_MACHINE" ]]; then
    log_error "the typed name differs"
    return 1
  fi
  "$OPT_PURGE_DATA" || return 0
  typed=$(ask_typed "--purge-data also deletes the lab's data. Type the machine name again to delete it:") \
    || return 1
  if [[ $typed != "$UNINSTALL_MACHINE" ]]; then
    log_error "the typed name differs"
    return 1
  fi
}

# offer_backup PLAN PINKA: before anything is removed, take a backup with
# 'pinka backup now' when the plan says one can be taken (a restic repository
# is configured and pinka is installed), unless --skip-backup or the operator
# declines (§5.4). Returns EXIT_UNINSTALL_REFUSED when the backup fails.
offer_backup() {
  "$OPT_SKIP_BACKUP" && return 0
  [[ $(json_field "$1" backup) == true ]] || return 0
  if "$INTERACTIVE" && ! confirm_yes "A restic repository is configured. Back up the lab first (pinka backup now)?"; then
    log_warn "no backup taken: declined"
    return 0
  fi
  log_info "backing up the lab first: $2 backup now"
  if ! "$2" backup now; then
    log_error "the backup failed (output above); nothing was removed: fix the backup, or re-run with --skip-backup"
    return "$EXIT_UNINSTALL_REFUSED"
  fi
}

# uninstall_vars_json MODE DIR: the variables of uninstall.yml: the host
# profile (DIR/host-profile.json) and this run's options; the plan in DIR.
uninstall_vars_json() {
  printf '{"host_profile": %s,\n' "$(cat -- "$2/host-profile.json")"
  cat <<JSON
 "pinka_uninstall": {
  "mode": "$1",
  "machine": "$(json_escape "$UNINSTALL_MACHINE")",
  "purge_data": $(json_bool "$OPT_PURGE_DATA"),
  "remove_components": $(json_list "$OPT_REMOVE_COMPONENTS"),
  "plan_file": "$(json_escape "$2/plan.json")",
  "plan_text": "$(json_escape "$2/plan.txt")",
  "report_file": "$(json_escape "$UNINSTALL_REPORT")",
  "started_at": "$(json_escape "$STARTED_AT")",
  "repo_commit": "$(json_escape "$GIT_COMMIT")",
  "log_file": "$(json_escape "$LOG_FILE")",
  "opt_existed": $(json_bool "$OPT_EXISTED")
 }
}
JSON
}

# run_uninstall_play MODE DIR: run uninstall.yml in MODE (plan or apply)
# with its working files in DIR. It runs without the group and host variables
# (ANSIBLE_VARS_ENABLED empty), so it needs neither the machine's files nor
# the vault (§5.4).
run_uninstall_play() {
  local rc=0 vars=$2/vars-$1.json
  local -a cmd=()
  (umask 077 && uninstall_vars_json "$1" "$2" >"$vars")
  cmd=("$ANSIBLE_VENV/bin/ansible-playbook" -i "$INFRA_DIR/ansible/inventory/hosts.yml"
    "$INFRA_DIR/ansible/uninstall.yml" -e "@$vars")
  log_info "running: ${cmd[*]}"
  (cd -- "$INFRA_DIR/ansible" \
    && ANSIBLE_CONFIG=$INFRA_DIR/ansible/ansible.cfg ANSIBLE_VARS_ENABLED='' LC_ALL=C.UTF-8 \
      LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 "${cmd[@]}") || rc=$?
  if ((rc != 0)); then
    log_error "ansible-playbook failed with exit status $rc; the failing task is above and in the log"
    return "$EXIT_ANSIBLE"
  fi
}

# report_summary REPORT: one line with the number of items per result.
report_summary() {
  pinka_python -c '
import collections, json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    items = json.load(handle).get("items", [])
counts = collections.Counter(item.get("result") for item in items)
print(", ".join(f"{n} {name}" for name, n in sorted(counts.items())) or "nothing")
' "$1"
}

# report_notes REPORT: what the owner needs after the uninstall, one line
# each: where the kept backups' key is, why to reboot, what to do afterwards,
# taking the kept secrets off the host first (owner, D2 review).
report_notes() {
  pinka_python -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
if report.get("backup_access"):
    print("kept backups: their key and settings are in %s (root only; %s/README.txt says how)"
          % (report["backup_access"], report.get("kept_dir", "")))
for reason in report.get("reboot_reasons") or []:
    print("reboot needed: " + reason + "; uninstall never reboots")
for step in report.get("follow_ups") or []:
    print("afterwards: " + step)
' "$1"
}

# remove_tooling DIR: remove /opt/pinka last, after the play that ran from
# it, and close the report's pending item with the result (§5.4).
remove_tooling() {
  local result=removed reason='removed last, after the play'
  if ! rm -rf -- "$1"; then
    result=failed
    reason='could not be removed; the error is in the log'
    log_error "$1 could not be removed; remove it yourself: rm -rf $1"
  fi
  [[ -f $UNINSTALL_REPORT ]] || return 0
  sed -i -e "s|$PENDING_ITEM|\"result\": \"$result\"|" \
    -e "s|\"reason\": \"$PENDING_REASON\"|\"reason\": \"$reason\"|" \
    -- "$UNINSTALL_REPORT"
}

# finish_uninstall: from the EXIT trap: with --json, print the report, or the
# plan when nothing was removed; then remove the working files.
finish_uninstall() {
  if "$OPT_JSON"; then
    if [[ -n $UNINSTALL_REPORT && -f $UNINSTALL_REPORT ]]; then
      cat -- "$UNINSTALL_REPORT" >&8
    elif [[ -f $UNINSTALL_DIR/plan.json ]]; then
      cat -- "$UNINSTALL_DIR/plan.json" >&8
    fi
  fi
  rm -rf -- "$UNINSTALL_DIR"
}

# stage_uninstall_profile: preflight, read-only and offline, for the family
# and the components the plan needs. Its outcome decides nothing: every
# host can be uninstalled.
stage_uninstall_profile() {
  local rc=0
  enter_stage preflight
  install -d -m 0700 "$UNINSTALL_DIR"
  "$INFRA_DIR/preflight/pinka-preflight" --no-write --quiet --offline \
    --output "$UNINSTALL_DIR/host-profile.json" --repo-commit "$GIT_COMMIT" || rc=$?
  case $rc in
    0 | 10 | 20 | 30) ;;
    *) fail "$EXIT_PREFLIGHT_ERROR" "preflight failed (exit status $rc); the traceback is above and in the log" ;;
  esac
  leave_stage
}

# uninstall_flow DIR REPORT_DIR STATE_FILE: compute and show the plan (in
# DIR); with --dry-run stop there; then offer a backup, take the typed
# confirmation, remove, remove /opt/pinka last and report in REPORT_DIR
# (§5.4). Sets FINAL_RC; a refusal ends bootstrap with 80.
uninstall_flow() {
  local dir=$1 plan=$1/plan.json refusal count summary notes line failed
  UNINSTALL_REPORT=$2/$(date -u +%Y%m%dT%H%M%SZ).json
  uninstall_machine "$3" || exit "$?"
  run_uninstall_play plan "$dir" || exit "$?"
  [[ -f $plan && -f $dir/plan.txt ]] || fail "$EXIT_INTERNAL" "uninstall.yml wrote no plan"
  cat -- "$dir/plan.txt"
  refusal=$(json_field "$plan" refusal)
  [[ -z $refusal ]] || fail "$EXIT_UNINSTALL_REFUSED" "uninstall refused: $refusal"
  count=$(json_field "$plan" to_remove)
  if "$OPT_DRY_RUN" || [[ $count == 0 ]]; then
    "$OPT_EXISTED" || rm -rf -- "$PINKA_OPT"
    if "$OPT_DRY_RUN"; then
      log_info "dry run: nothing was removed"
    else
      log_info "nothing of pinka's is on this host; nothing was removed"
    fi
    FINAL_RC=$EXIT_OK
    return 0
  fi
  [[ -n $UNINSTALL_MACHINE ]] || fail "$EXIT_UNINSTALL_REFUSED" \
    "$3 is missing, so the machine name is unknown: pass --machine NAME, the name this host was installed as"
  offer_backup "$plan" "$PINKA_LAB" || exit "$?"
  uninstall_confirmed || fail "$EXIT_UNINSTALL_REFUSED" "uninstall not confirmed; nothing was removed"
  # Logging out a node pinka joined ends an SSH session over the tailnet:
  # uninstall goes on without its terminal, and its report says how it ended.
  trap '' HUP
  run_uninstall_play apply "$dir" || exit "$?"
  [[ -f $UNINSTALL_REPORT ]] || fail "$EXIT_INTERNAL" "uninstall.yml wrote no report"
  summary=$(report_summary "$UNINSTALL_REPORT")
  notes=$(report_notes "$UNINSTALL_REPORT")
  if [[ -n ${PINKA_TOOLING_HANDOFF:-} ]]; then
    printf '%s\n' "$UNINSTALL_REPORT" >"$PINKA_TOOLING_HANDOFF"
    log_info "$PINKA_OPT is removed when the installer, which runs from it, ends"
  else
    remove_tooling "$PINKA_OPT"
  fi
  failed=$(grep -c '"result": "failed"' -- "$UNINSTALL_REPORT" || true)
  log_info "uninstall: $summary (before $PINKA_OPT); report: $UNINSTALL_REPORT"
  while IFS= read -r line; do
    [[ -z $line ]] || log_warn "$line"
  done <<<"$notes"
  if ((failed > 0)); then
    log_error "$failed item(s) could not be removed; the report names each and why"
    FINAL_RC=$EXIT_UNINSTALL_KEPT
  else
    FINAL_RC=$EXIT_OK
  fi
}

# stage_uninstall: bootstrap.sh --uninstall after its tooling (§5.4).
stage_uninstall() {
  enter_stage uninstall
  uninstall_flow "$UNINSTALL_DIR" "$UNINSTALL_REPORT_DIR" "$STATE_FILE"
  leave_stage
}
