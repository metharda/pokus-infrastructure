# shellcheck shell=bash
# shellcheck disable=SC2034 # run-time state is read by the traps in bootstrap.sh
# Managed by pokus-infra (bootstrap/lib/stages.sh). Local changes will be overwritten.
#
# The stages of bootstrap.sh (BUILD_PROMPT §5.2) and the decisions between
# them. A stage either returns normally or ends bootstrap with an exit code
# from exitcodes.sh: deliberate failures call `fail CODE MESSAGE`, library
# functions return their code and the ERR trap in bootstrap.sh passes it on.
# The decision helpers (gate_check, playbook_result, profile_change_accepted,
# state_json) touch nothing on the host and are unit-tested.
#
# Expects INFRA_DIR, BOOTSTRAP_DIR and INTERACTIVE to be set by bootstrap.sh.

readonly RUN_DIR=/run/pokus/bootstrap
readonly SIGNAL_FILE=/run/pokus/bootstrap-signal.json
readonly PROFILE_FILE=/etc/pokus/host-profile.json
readonly STATE_FILE=/etc/pokus/bootstrap-state.json
readonly STORED_VAULT_PASS=/etc/pokus/secrets/vault-pass
readonly LOG_DIR=/var/log/pokus-bootstrap
readonly LOCK_FILE=/run/lock/pokus-bootstrap.lock
readonly POKUS_LAB=/usr/local/bin/pokus-lab
readonly SIGNAL_CODES=' 75 76 77 '

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

# pokus_python ARGS...: the pinned CPython (replaced in unit tests).
pokus_python() {
  "$POKUS_PYTHON" "$@"
}

# profile_tool ARGS...: python -m pokus_preflight.profile_tool with the pinned CPython.
profile_tool() {
  PYTHONPATH="$INFRA_DIR/preflight" pokus_python -m pokus_preflight.profile_tool "$@"
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
  pokus_python -c '
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

# read_signal FILE: print the signal code on the first line and the message
# after it; false if FILE is missing or not a valid signal.
read_signal() {
  [[ -f $1 ]] || return 1
  pokus_python -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
    code = int(data["code"])
    message = str(data.get("message", ""))
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
print(code)
print(message)
' "$1"
}

# schedule_reboot: after a reboot signal (76) with --allow-reboot, reboot in one
# minute, so that bootstrap still writes its state file and exits with 76
# (BUILD_PROMPT §3.2, §13.7). Never in check mode.
schedule_reboot() {
  if "$OPT_CHECK"; then
    log_info "check mode: not rebooting"
    return 0
  fi
  if shutdown -r +1 "Pokus bootstrap: reboot required (--allow-reboot)"; then
    log_warn "the host reboots in one minute (--allow-reboot); run bootstrap again afterwards"
  else
    log_error "could not schedule the reboot; reboot the host yourself, then run bootstrap again"
  fi
}

# playbook_result RC SIGNAL_FILE: map an ansible-playbook exit status and the
# bootstrap signal file to a bootstrap exit code (BUILD_PROMPT §5.2 step 9).
# Sets SIGNAL_CODE and SIGNAL_MESSAGE for a valid signal.
playbook_result() {
  local rc=$1 file=$2 text code
  if ((rc == 0)); then
    if [[ -f $file ]]; then
      log_warn "ignoring $file: the play succeeded"
    fi
    return 0
  fi
  if text=$(read_signal "$file"); then
    code=${text%%$'\n'*}
    if [[ $SIGNAL_CODES == *" $code "* ]]; then
      SIGNAL_CODE=$code
      SIGNAL_MESSAGE=${text#*$'\n'}
      log_error "$SIGNAL_MESSAGE"
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

# state_json RC FINISHED_AT: print /etc/pokus/bootstrap-state.json (BUILD_PROMPT §5.2 step 12).
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
 "pokus_bootstrap": {
  "machine": "$(json_escape "$OPT_MACHINE")",
  "repo_commit": "$(json_escape "$GIT_COMMIT")",
  "infra_tree": "$(json_escape "$GIT_INFRA_TREE")",
  "infra_dirty": $(json_bool "$GIT_INFRA_DIRTY"),
  "ref": "$(json_escape "${OPT_REF:-HEAD}")",
  "check": $(json_bool "$OPT_CHECK"),
  "refresh_profile": $(json_bool "$OPT_REFRESH_PROFILE"),
  "allow_reboot": $(json_bool "$OPT_ALLOW_REBOOT"),
  "adopt_incus": $(json_bool "$OPT_ADOPT_INCUS"),
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

# stage_logging: tee all output to /var/log/pokus-bootstrap/<UTC>.log (0600)
# and take the bootstrap lock.
stage_logging() {
  STARTED_AT=$(utc_now)
  install -d -m 0700 "$LOG_DIR"
  LOG_FILE=$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ).log
  (umask 077 && : >>"$LOG_FILE")
  exec > >(tee -a -- "$LOG_FILE") 2>&1
  TEE_PID=$!
  install -d -m 0755 "$(dirname -- "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    fail "$EXIT_OPERATOR" "another bootstrap is running on this host; wait for it to finish"
  fi
  log_info "bootstrap started; log: $LOG_FILE"
}

# stage_git: record the checkout (B3) and refuse a dirty infra/ tree (§5.2 step 1).
stage_git() {
  local rc=0
  enter_stage git
  command -v git >/dev/null 2>&1 \
    || fail "$EXIT_NEEDS_ACTION" "git is not installed; install it with the distribution's package manager"
  git_state "$INFRA_DIR" || rc=$?
  case $rc in
    0) ;;
    2) fail "$EXIT_OPERATOR" "$GIT_REPO_ROOT must be owned by root or by the user running sudo" ;;
    *) fail "$EXIT_OPERATOR" "bootstrap must run from a git checkout of the Pokus repository" ;;
  esac
  [[ -n $GIT_COMMIT && -n $GIT_INFRA_TREE ]] \
    || fail "$EXIT_OPERATOR" "cannot read the checked-out commit of $GIT_REPO_ROOT"
  STATE_ENABLED=true
  "$OPT_CHECK" && STATE_ENABLED=false
  if [[ -n $OPT_REF ]] && ! ref_matches_head "$OPT_REF"; then
    fail "$EXIT_OPERATOR" "--ref $OPT_REF is not the checked-out commit $GIT_COMMIT; check it out first"
  fi
  if "$GIT_INFRA_DIRTY"; then
    "$OPT_ALLOW_DIRTY" || fail "$EXIT_OPERATOR" \
      "infra/ has uncommitted changes; commit them, or re-run with --allow-dirty (recorded)"
    log_warn "infra/ has uncommitted changes (--allow-dirty); this is recorded"
  fi
  log_info "repository commit $GIT_COMMIT, infra tree $GIT_INFRA_TREE"
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

# stage_tooling: pinned uv and CPython under /opt/pokus (§5.2 steps 2 and 4, §19.3).
stage_tooling() {
  enter_stage tooling
  install -d -m 0755 "$POKUS_OPT"
  install_uv
  install_python
  leave_stage
}

# stage_frontend: hand over to the installer TUI (§5.3). Temporary until infra
# step 13 adds the installer: forcing the TUI or replaying an answers file
# stops with 77; the default falls back to the plain front-end.
stage_frontend() {
  enter_stage frontend
  if [[ -n $OPT_ANSWERS ]]; then
    fail "$EXIT_OPERATOR" "answers files are applied by the installer, which this version does not include yet; run without --answers"
  fi
  if "$USE_TUI"; then
    [[ $OPT_TUI == on ]] \
      && fail "$EXIT_OPERATOR" "this version does not include the installer TUI yet; re-run with --no-tui"
    log_info "the installer TUI is not included in this version; continuing without it"
  fi
  leave_stage
}

# stage_preflight: run preflight, apply the gate, check for a changed host
# profile and store the profile (§2.4, §2.6, §5.2 step 3).
stage_preflight() {
  local rc=0 outcome tier needs_action changes
  local -a args=(--no-write --output "$RUN_DIR/host-profile.json" --repo-commit "$GIT_COMMIT")
  enter_stage preflight
  install -d -m 0700 "$RUN_DIR"
  "$OPT_ACCEPT_DEGRADED" && args+=(--accept-degraded)
  "$OPT_ACCEPT_EXPERIMENTAL" && args+=(--accept-experimental)
  "$OPT_FORCE_UNSUPPORTED" && args+=(--force-unsupported)
  "$OPT_ADOPT_INCUS" && args+=(--adopt-incus)
  "$OPT_ALLOW_DIRTY" && args+=(--allow-dirty)
  "$OPT_OFFLINE" && args+=(--offline)
  "$INFRA_DIR/preflight/pokus-preflight" "${args[@]}" || rc=$?
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
  install_collections "$INFRA_DIR/ansible/requirements.yml"
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
    && ANSIBLE_CONFIG=$INFRA_DIR/ansible/ansible.cfg LC_ALL=C.UTF-8 LANG=C.UTF-8 "${cmd[@]}") || rc=$?
  playbook_result "$rc" "$SIGNAL_FILE" || exit "$?"
}

# stage_ansible: the provisioning play (§5.2 step 8).
stage_ansible() {
  enter_stage ansible
  run_playbook site.yml
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
  [[ -x $POKUS_LAB ]] || fail "$EXIT_ANSIBLE" "$POKUS_LAB is not installed; the play has not provisioned the doctor yet"
  "$POKUS_LAB" doctor || rc=$?
  case $rc in
    0 | 1 | 2) FINAL_RC=$rc ;;
    *)
      log_error "doctor ended with exit status $rc; counted as failures"
      FINAL_RC=$EXIT_DOCTOR_FAILURES
      ;;
  esac
  leave_stage
}
