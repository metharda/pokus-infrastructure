# shellcheck shell=bash
# shellcheck disable=SC2034 # GIT_* values are read by the scripts that source this file
# Managed by pinka (bootstrap/lib/gitstate.sh). Local changes will be overwritten.
#
# Git provenance of the infra checkout (BUILD_PROMPT §5.2 step 1; owner answer
# B3): the repository commit, the tree hash of infra/, and whether infra/ has
# uncommitted changes. Platform and catalog are deployed from tags through a
# mirror, so changes outside infra/ do not make the tree dirty.
#
# Git runs as root in a checkout that usually belongs to the admin who invoked
# sudo. The checkout must be owned by root or by that user (SUDO_UID); only then
# is it marked as a safe directory, and fsmonitor hooks are always disabled, so
# a checkout prepared by another account cannot run code as root.

GIT_REPO_ROOT=''
GIT_COMMIT=''
GIT_INFRA_TREE=''
GIT_INFRA_DIRTY=false
GIT_INFRA_SUBDIR=''

# find_repo_root DIR: print the nearest ancestor of DIR that contains .git.
find_repo_root() {
  local dir
  dir=$(cd -- "$1" && pwd -P) || return 1
  while :; do
    if [[ -e $dir/.git ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
    [[ $dir == / ]] && return 1
    dir=$(dirname -- "$dir")
  done
}

# owner_allowed PATH: true if PATH is owned by root or by the sudo user.
owner_allowed() {
  local owner
  owner=$(stat -c %u -- "$1") || return 1
  [[ $owner == 0 || $owner == "${SUDO_UID:-0}" ]]
}

# git_in ARGS...: run git in GIT_REPO_ROOT with the safety settings above.
git_in() {
  git -c safe.directory="$GIT_REPO_ROOT" -c core.fsmonitor=false -C "$GIT_REPO_ROOT" "$@"
}

# git_state INFRA_DIR: set the GIT_* variables.
# Returns 1 outside a git checkout, 2 if the checkout has an untrusted owner.
git_state() {
  local infra_dir real_infra
  real_infra=$(cd -- "$1" && pwd -P) || return 1
  GIT_REPO_ROOT=$(find_repo_root "$real_infra") || return 1
  owner_allowed "$GIT_REPO_ROOT" || return 2
  git_in rev-parse --git-dir >/dev/null 2>&1 || return 1
  infra_dir=${real_infra#"$GIT_REPO_ROOT"}
  GIT_INFRA_SUBDIR=${infra_dir#/}
  GIT_COMMIT=$(git_in rev-parse HEAD)
  if [[ -n $GIT_INFRA_SUBDIR ]]; then
    GIT_INFRA_TREE=$(git_in rev-parse "HEAD:$GIT_INFRA_SUBDIR")
  else
    GIT_INFRA_TREE=$(git_in rev-parse "HEAD^{tree}")
  fi
  if [[ -n $(git_in status --porcelain --untracked-files=normal -- "${GIT_INFRA_SUBDIR:-.}") ]]; then
    GIT_INFRA_DIRTY=true
  else
    GIT_INFRA_DIRTY=false
  fi
}

# ref_matches_head REF: true if REF names the checked-out commit.
ref_matches_head() {
  local commit
  commit=$(git_in rev-parse --verify --quiet "$1^{commit}") || return 1
  [[ $commit == "$GIT_COMMIT" ]]
}
