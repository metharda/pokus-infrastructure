# shellcheck shell=bash
# shellcheck disable=SC2034 # OPT_* values are read by the scripts that source this file
# Managed by pokus-infra (bootstrap/lib/args.sh). Local changes will be overwritten.
#
# Command-line interface of bootstrap.sh (BUILD_PROMPT §5.1). The usage text is
# the source of docs/reference/cli/bootstrap.md (tools/gen runs --help).

usage() {
  cat <<'EOF'
Usage: sudo ./bootstrap/bootstrap.sh [--machine <name>] [options]

Provision this host as a Pokus lab host: pre-preflight, preflight gate, pinned
uv, CPython and Ansible, the Ansible play, optional restore, and doctor. On a
terminal the installer TUI asks for everything; --answers replays its answers.

Options:
  --machine NAME           Selects ansible/inventory/host_vars/NAME.yml; required unless --answers or the TUI names it
  --ref REF                Git ref of pokus-infra being applied (recorded; default: current HEAD)
  --restore                After provisioning, run restore.yml (prompts for snapshot)
  --tags T1,T2             Pass through to ansible-playbook
  --skip-tags T1,T2        e.g. images,models for a fast first run
  --check                  Ansible check mode (dry run); preflight still runs
  --accept-degraded        Continue when optional features are missing
  --accept-experimental    Continue on an experimental-tier OS
  --force-unsupported      Continue on an unsupported host (recorded in every fingerprint)
  --refresh-profile        Accept a changed host profile
  --allow-reboot           Allow automatic reboot on bare metal / VM
  --allow-dirty            Continue with uncommitted changes in infra/ (recorded)
  --adopt-incus            Adopt an existing Incus installation
  --accept-mok-enrollment  With Secure Boot and install_nvidia_driver: auto, install the NVIDIA driver and import a Machine Owner Key (recorded)
  --mok-password-stdin     Read the one-time MOK password from the first line of standard input
  --allow-community-repos  Permit non-vendor, non-distro repositories (COPR, OBS, RPM Fusion)
  --offline                Skip network reachability checks (for testing only)
  --non-interactive        Never prompt; fail where a prompt would be needed
  --vault-password-file F  Use a file instead of prompting
  --answers FILE           Non-secret answers file (as exported by the installer TUI)
  --tui | --no-tui         Force or disable the TUI (default: TUI when stdin and stdout are TTYs)
  -h, --help               Show this help and exit

Exit codes: see docs/reference/exit-codes.md (0 success; 1/2 doctor warnings/failures;
10, 11, 20, 30, 40 preflight gate; 50 tooling; 60 play; 64 usage; 70 internal;
75 WSL restart; 76 reboot; 77 operator action).
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
OPT_ACCEPT_MOK_ENROLLMENT=false
OPT_MOK_PASSWORD_STDIN=false
OPT_ALLOW_COMMUNITY_REPOS=false
OPT_OFFLINE=false
OPT_NON_INTERACTIVE=false
OPT_VAULT_PASSWORD_FILE=''
OPT_ANSWERS=''
OPT_TUI=auto
OPT_HELP=false

readonly MACHINE_RE='^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'
readonly TAGS_RE='^[a-z0-9_]+(,[a-z0-9_]+)*$'

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
parse_args() {
  while (($# > 0)); do
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
      --restore) OPT_RESTORE=true && shift ;;
      --check) OPT_CHECK=true && shift ;;
      --accept-degraded) OPT_ACCEPT_DEGRADED=true && shift ;;
      --accept-experimental) OPT_ACCEPT_EXPERIMENTAL=true && shift ;;
      --force-unsupported) OPT_FORCE_UNSUPPORTED=true && shift ;;
      --refresh-profile) OPT_REFRESH_PROFILE=true && shift ;;
      --allow-reboot) OPT_ALLOW_REBOOT=true && shift ;;
      --allow-dirty) OPT_ALLOW_DIRTY=true && shift ;;
      --adopt-incus) OPT_ADOPT_INCUS=true && shift ;;
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

# validate_args: check values and combinations after parsing.
validate_args() {
  "$OPT_HELP" && return 0
  if [[ -z $OPT_MACHINE && -z $OPT_ANSWERS ]] && ! use_tui; then
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

# use_tui: true if the installer TUI should take over (BUILD_PROMPT §5.3).
use_tui() {
  case $OPT_TUI in
    on) return 0 ;;
    off) return 1 ;;
    *) is_interactive && [[ -z $OPT_ANSWERS ]] ;;
  esac
}
