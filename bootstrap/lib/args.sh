# shellcheck shell=bash
# shellcheck disable=SC2034 # OPT_* values are read by the scripts that source this file
# Managed by pinka (bootstrap/lib/args.sh). Local changes will be overwritten.
#
# Command-line interface of bootstrap.sh (BUILD_PROMPT §5.1). The usage text is
# the source of docs/reference/cli/bootstrap.md (tools/gen runs --help).

usage() {
  cat <<'EOF'
Usage: sudo ./bootstrap/bootstrap.sh [--machine <name>] [options]
       sudo ./bootstrap/bootstrap.sh --uninstall [uninstall options]

Provision this host as a Pokus lab host: pre-preflight, preflight gate, pinned
uv, CPython and Ansible, the Ansible play, optional restore, and doctor. On a
terminal the installer TUI asks for everything; --answers replays its answers.
With --uninstall, remove what pinka installed instead and keep the lab's data.

Options:
  --machine NAME           Selects ansible/inventory/host_vars/NAME.yml; required unless --answers, --uninstall or the TUI names it
  --ref REF                Git ref of pokus-infrastructure being applied (recorded; default: current HEAD)
  --restore                After provisioning, run restore.yml (prompts for snapshot)
  --tags T1,T2             Pass through to ansible-playbook
  --skip-tags T1,T2        e.g. images,models for a fast first run
  --check                  Ansible check mode (dry run); preflight still runs
  --accept-degraded        Continue when optional features are missing
  --accept-experimental    Continue on an experimental-tier OS
  --force-unsupported      Continue on an unsupported host (recorded in every fingerprint)
  --refresh-profile        Accept a changed host profile
  --allow-reboot           Allow automatic reboot on bare metal / VM
  --allow-dirty            Continue with uncommitted changes in the repository (recorded)
  --adopt-incus            Adopt an existing Incus installation
  --inspect-platform       Fetch, stage and validate the platform release, print its summary, change nothing else
  --replace-platform NAME  Allow a platform with another name to replace the active platform NAME
  --accept-mok-enrollment  With Secure Boot and install_nvidia_driver: auto, install the NVIDIA driver and import a Machine Owner Key (recorded)
  --mok-password-stdin     Read the one-time MOK password from the first line of standard input
  --allow-community-repos  Permit non-vendor, non-distro repositories (COPR, OBS, RPM Fusion)
  --offline                Skip network reachability checks (for testing only)
  --non-interactive        Never prompt; fail where a prompt would be needed
  --vault-password-file F  Use a file instead of prompting
  --answers FILE           Non-secret answers file (as exported by the installer TUI)
  --tui | --no-tui         Force or disable the TUI (default: TUI when stdin and stdout are TTYs)
  -h, --help               Show this help and exit

Uninstall options (with --uninstall, only --machine, --allow-dirty, --offline,
--non-interactive and --no-tui of the options above apply):
  --uninstall              Remove what pinka installed and keep the lab's data
  --dry-run                Print the removal plan and change nothing
  --purge-data             Also remove the lab's data (the typed machine name is asked for again)
  --confirm-machine NAME   The typed machine name, for --non-interactive
  --skip-backup            Do not offer a backup first
  --remove-component C     On a host without an install record, also remove C:
                           docker, incus, tailscale or nvidia (repeatable)
  --json                   Print the plan (with --dry-run) or the report as JSON on standard output

Exit codes: see docs/reference/exit-codes.md (0 success; 1/2 doctor warnings/failures;
10, 11, 20, 30, 40 preflight gate; 50 tooling; 60 play; 64 usage; 65 platform
release refused; 70 internal; 75 WSL restart; 76 reboot; 77 operator action;
80 uninstall refused or not confirmed; 81 uninstall kept items it could not remove).
EOF
}

# Option values (set by parse_args; read-only afterwards).
OPT_MACHINE=''
OPT_REF=''
OPT_RESTORE=false
OPT_TAGS=''
OPT_SKIP_TAGS=''
OPT_CHECK=false
OPT_ACCEPT_DEGRADED=false
OPT_ACCEPT_EXPERIMENTAL=false
OPT_FORCE_UNSUPPORTED=false
OPT_REFRESH_PROFILE=false
OPT_ALLOW_REBOOT=false
OPT_ALLOW_DIRTY=false
OPT_ADOPT_INCUS=false
OPT_INSPECT_PLATFORM=false
OPT_REPLACE_PLATFORM=''
OPT_ACCEPT_MOK_ENROLLMENT=false
OPT_MOK_PASSWORD_STDIN=false
OPT_ALLOW_COMMUNITY_REPOS=false
OPT_OFFLINE=false
OPT_NON_INTERACTIVE=false
OPT_VAULT_PASSWORD_FILE=''
OPT_ANSWERS=''
OPT_TUI=auto
OPT_HELP=false
OPT_UNINSTALL=false
OPT_DRY_RUN=false
OPT_PURGE_DATA=false
OPT_CONFIRM_MACHINE=''
OPT_SKIP_BACKUP=false
OPT_REMOVE_COMPONENTS=''
OPT_JSON=false

readonly MACHINE_RE='^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'
readonly TAGS_RE='^[a-z0-9_]+(,[a-z0-9_]+)*$'
readonly PLATFORM_NAME_RE='^[a-z][a-z0-9]{1,11}$'
# The shared components --remove-component may name (BUILD_PROMPT §5.4).
readonly REMOVABLE_COMPONENTS=' docker incus tailscale nvidia '

# usage_error MESSAGE: print the message and a hint, return EXIT_USAGE.
usage_error() {
  log_error "$1"
  log_error "run '$0 --help' for usage"
  return "$EXIT_USAGE"
}

# need_value OPTION ARGC: fail unless the option has a value.
need_value() {
  if (($2 < 2)); then
    usage_error "option $1 needs a value"
    return "$EXIT_USAGE"
  fi
}

# parse_args "$@": set the OPT_* variables; returns EXIT_USAGE on errors.
# An option with a value may also be given as --option=value (the installer
# TUI passes --tags=, --skip-tags= and --replace-platform= on that way).
parse_args() {
  while (($# > 0)); do
    if [[ $1 == --*=* ]]; then
      case ${1%%=*} in
        --machine | --ref | --tags | --skip-tags | --vault-password-file | --answers | --replace-platform | \
          --confirm-machine | --remove-component)
          set -- "${1%%=*}" "${1#*=}" "${@:2}"
          ;;
        *)
          usage_error "option ${1%%=*} takes no value"
          return
          ;;
      esac
    fi
    case $1 in
      --machine)
        need_value "$1" $# || return
        OPT_MACHINE=$2
        shift 2
        ;;
      --ref)
        need_value "$1" $# || return
        OPT_REF=$2
        shift 2
        ;;
      --tags)
        need_value "$1" $# || return
        OPT_TAGS=$2
        shift 2
        ;;
      --skip-tags)
        need_value "$1" $# || return
        OPT_SKIP_TAGS=$2
        shift 2
        ;;
      --vault-password-file)
        need_value "$1" $# || return
        OPT_VAULT_PASSWORD_FILE=$2
        shift 2
        ;;
      --answers)
        need_value "$1" $# || return
        OPT_ANSWERS=$2
        shift 2
        ;;
      --replace-platform)
        need_value "$1" $# || return
        OPT_REPLACE_PLATFORM=$2
        shift 2
        ;;
      --confirm-machine)
        need_value "$1" $# || return
        OPT_CONFIRM_MACHINE=$2
        shift 2
        ;;
      --remove-component)
        need_value "$1" $# || return
        OPT_REMOVE_COMPONENTS+="${OPT_REMOVE_COMPONENTS:+,}$2"
        shift 2
        ;;
      --uninstall) OPT_UNINSTALL=true && shift ;;
      --dry-run) OPT_DRY_RUN=true && shift ;;
      --purge-data) OPT_PURGE_DATA=true && shift ;;
      --skip-backup) OPT_SKIP_BACKUP=true && shift ;;
      --json) OPT_JSON=true && shift ;;
      --restore) OPT_RESTORE=true && shift ;;
      --check) OPT_CHECK=true && shift ;;
      --accept-degraded) OPT_ACCEPT_DEGRADED=true && shift ;;
      --accept-experimental) OPT_ACCEPT_EXPERIMENTAL=true && shift ;;
      --force-unsupported) OPT_FORCE_UNSUPPORTED=true && shift ;;
      --refresh-profile) OPT_REFRESH_PROFILE=true && shift ;;
      --allow-reboot) OPT_ALLOW_REBOOT=true && shift ;;
      --allow-dirty) OPT_ALLOW_DIRTY=true && shift ;;
      --adopt-incus) OPT_ADOPT_INCUS=true && shift ;;
      --inspect-platform) OPT_INSPECT_PLATFORM=true && shift ;;
      --accept-mok-enrollment) OPT_ACCEPT_MOK_ENROLLMENT=true && shift ;;
      --mok-password-stdin) OPT_MOK_PASSWORD_STDIN=true && shift ;;
      --allow-community-repos) OPT_ALLOW_COMMUNITY_REPOS=true && shift ;;
      --offline) OPT_OFFLINE=true && shift ;;
      --non-interactive) OPT_NON_INTERACTIVE=true && shift ;;
      --tui) OPT_TUI=on && shift ;;
      --no-tui) OPT_TUI=off && shift ;;
      -h | --help) OPT_HELP=true && shift ;;
      *)
        usage_error "unknown option: $1"
        return
        ;;
    esac
  done
  validate_args
}

# validate_uninstall_args: the options of --uninstall (§5.4) and what it excludes.
validate_uninstall_args() {
  local component option
  local -a components=()
  if ! "$OPT_UNINSTALL"; then
    for option in DRY_RUN:--dry-run PURGE_DATA:--purge-data SKIP_BACKUP:--skip-backup JSON:--json; do
      local name=OPT_${option%%:*}
      if "${!name}"; then
        usage_error "${option#*:} applies only with --uninstall"
        return
      fi
    done
    if [[ -n $OPT_CONFIRM_MACHINE || -n $OPT_REMOVE_COMPONENTS ]]; then
      usage_error "--confirm-machine and --remove-component apply only with --uninstall"
      return
    fi
    return 0
  fi
  for option in RESTORE:--restore CHECK:--check ACCEPT_DEGRADED:--accept-degraded \
    ACCEPT_EXPERIMENTAL:--accept-experimental FORCE_UNSUPPORTED:--force-unsupported \
    REFRESH_PROFILE:--refresh-profile ALLOW_REBOOT:--allow-reboot ADOPT_INCUS:--adopt-incus \
    INSPECT_PLATFORM:--inspect-platform ACCEPT_MOK_ENROLLMENT:--accept-mok-enrollment \
    MOK_PASSWORD_STDIN:--mok-password-stdin ALLOW_COMMUNITY_REPOS:--allow-community-repos; do
    local name=OPT_${option%%:*}
    if "${!name}"; then
      usage_error "--uninstall excludes ${option#*:}"
      return
    fi
  done
  for option in "--ref:$OPT_REF" "--tags:$OPT_TAGS" "--skip-tags:$OPT_SKIP_TAGS" \
    "--replace-platform:$OPT_REPLACE_PLATFORM" "--answers:$OPT_ANSWERS" \
    "--vault-password-file:$OPT_VAULT_PASSWORD_FILE"; do
    if [[ -n ${option#*:} ]]; then
      usage_error "--uninstall excludes ${option%%:*}"
      return
    fi
  done
  if [[ $OPT_TUI == on ]]; then
    usage_error "--uninstall excludes --tui (the installer TUI offers uninstall on its Install mode screen)"
    return
  fi
  if [[ -n $OPT_CONFIRM_MACHINE && ! $OPT_CONFIRM_MACHINE =~ $MACHINE_RE ]]; then
    usage_error "invalid machine name '$OPT_CONFIRM_MACHINE' for --confirm-machine"
    return
  fi
  [[ -n $OPT_REMOVE_COMPONENTS ]] && IFS=, read -r -a components <<<"$OPT_REMOVE_COMPONENTS"
  for component in "${components[@]}"; do
    if [[ $REMOVABLE_COMPONENTS != *" $component "* ]]; then
      usage_error "--remove-component takes docker, incus, tailscale or nvidia, not '$component'"
      return
    fi
  done
  return 0
}

# validate_args: check values and combinations after parsing.
validate_args() {
  "$OPT_HELP" && return 0
  validate_uninstall_args || return
  if [[ -z $OPT_MACHINE && -z $OPT_ANSWERS ]] && ! "$OPT_UNINSTALL" && ! use_tui; then
    usage_error "--machine is required (or --answers, or the installer TUI on a terminal)"
    return
  fi
  if [[ -n $OPT_MACHINE && ! $OPT_MACHINE =~ $MACHINE_RE ]]; then
    usage_error "invalid machine name '$OPT_MACHINE' (lower-case letters, digits and '-', at most 63 characters)"
    return
  fi
  local tags
  for tags in "$OPT_TAGS" "$OPT_SKIP_TAGS"; do
    if [[ -n $tags && ! $tags =~ $TAGS_RE ]]; then
      usage_error "invalid tag list '$tags' (comma-separated lower-case names)"
      return
    fi
  done
  if [[ $OPT_TUI == on ]] && "$OPT_NON_INTERACTIVE"; then
    usage_error "--tui and --non-interactive exclude each other"
    return
  fi
  if [[ $OPT_TUI == on ]] && "$OPT_MOK_PASSWORD_STDIN"; then
    usage_error "--tui and --mok-password-stdin exclude each other (the TUI asks for the password)"
    return
  fi
  if [[ -n $OPT_REPLACE_PLATFORM && ! $OPT_REPLACE_PLATFORM =~ $PLATFORM_NAME_RE ]]; then
    usage_error "invalid platform name '$OPT_REPLACE_PLATFORM' (2-12 lower-case letters and digits)"
    return
  fi
  if "$OPT_INSPECT_PLATFORM"; then
    if "$OPT_CHECK" || "$OPT_RESTORE" || [[ -n $OPT_TAGS || -n $OPT_SKIP_TAGS ]]; then
      usage_error "--inspect-platform excludes --check, --restore, --tags and --skip-tags"
      return
    fi
  fi
  if [[ -n $OPT_ANSWERS && ! -r $OPT_ANSWERS ]]; then
    usage_error "answers file '$OPT_ANSWERS' is not readable"
    return
  fi
  if [[ -n $OPT_VAULT_PASSWORD_FILE && ! -r $OPT_VAULT_PASSWORD_FILE ]]; then
    usage_error "vault password file '$OPT_VAULT_PASSWORD_FILE' is not readable"
    return
  fi
  return 0
}

# is_interactive: true if prompting is allowed (TTYs and no --non-interactive).
is_interactive() {
  ! "$OPT_NON_INTERACTIVE" && [[ -t 0 && -t 1 ]]
}

# use_tui: true if the installer TUI should take over (BUILD_PROMPT §5.3);
# never for --uninstall, which asks its own questions (§5.4).
use_tui() {
  "$OPT_UNINSTALL" && return 1
  case $OPT_TUI in
    on) return 0 ;;
    off) return 1 ;;
    *) is_interactive && [[ -z $OPT_ANSWERS ]] ;;
  esac
}
