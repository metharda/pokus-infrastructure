# shellcheck shell=bash
# Managed by pokus-infra (bootstrap/lib/log.sh). Local changes will be overwritten.
#
# Logging with UTC timestamps (BUILD_PROMPT §14) and the JSON event stream for
# the installer TUI (BUILD_PROMPT §18.5): when POKUS_EVENTS_FILE is set, every
# stage start, stage end and bootstrap signal is appended as one JSON line.

utc_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

log_info() {
  printf '%s [INFO] %s\n' "$(utc_now)" "$*"
}

log_warn() {
  printf '%s [WARN] %s\n' "$(utc_now)" "$*" >&2
}

log_error() {
  printf '%s [ERROR] %s\n' "$(utc_now)" "$*" >&2
}

# json_escape TEXT: print TEXT as the body of a JSON string (without quotes).
json_escape() {
  local text=$1 out='' ch i code
  for ((i = 0; i < ${#text}; i++)); do
    ch=${text:i:1}
    case $ch in
      '"') out+='\"' ;;
      \\) out+="\\\\" ;;
      $'\n') out+='\n' ;;
      $'\r') out+='\r' ;;
      $'\t') out+='\t' ;;
      *)
        printf -v code '%d' "'$ch"
        if ((code >= 0 && code < 32)); then
          printf -v ch '\\u%04x' "$code"
        fi
        out+=$ch
        ;;
    esac
  done
  printf '%s' "$out"
}

# emit_event TYPE STAGE STATUS [EXIT_CODE] [MESSAGE]: append one event line.
emit_event() {
  local type=$1 stage=$2 status=$3 code=${4:-} message=${5:-}
  [[ -n ${POKUS_EVENTS_FILE:-} ]] || return 0
  local line
  line=$(printf '{"ts":"%s","type":"%s","stage":"%s","status":"%s"' \
    "$(utc_now)" "$(json_escape "$type")" "$(json_escape "$stage")" "$(json_escape "$status")")
  if [[ -n $code ]]; then
    line+=$(printf ',"exit_code":%d' "$code")
  fi
  if [[ -n $message ]]; then
    line+=$(printf ',"message":"%s"' "$(json_escape "$message")")
  fi
  printf '%s}\n' "$line" >>"$POKUS_EVENTS_FILE"
}

# stage_begin NAME / stage_end NAME STATUS [CODE] [MESSAGE]: log and emit.
stage_begin() {
  log_info "== stage $1"
  emit_event bootstrap.stage "$1" started
}

stage_end() {
  emit_event bootstrap.stage "$1" "$2" "${3:-}" "${4:-}"
}
