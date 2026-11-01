# shellcheck shell=bash
# shellcheck disable=SC2034 # GIT_* values are read by the scripts that source this file
# Managed by pinka (bootstrap/lib/gitstate.sh). Local changes will be overwritten.
#
# Git provenance of the checkout: the repository
# commit, the tree hash of the whole repository, and whether the repository
# has uncommitted changes. Bootstrap runs only from the root of its own
# checkout; a directory inside another repository is no checkout of
# pokus-infrastructure.
#
# Bootstrap also runs from an unpacked release package:
# its release/RELEASE.json records the tag, the commit and the tree
# hash; the tree hash of the unpacked files is computed here with git (a
# temporary index with the tree's own .gitignore and .gitattributes, release/
# left out, no user or system configuration), the same computation as
# tools/lockcheck/pinka_lockcheck/bundle.py, and a difference counts as a
# dirty tree.
#
# Git runs as root in a checkout that usually belongs to the admin who invoked
# sudo. The checkout must be owned by root or by that user (SUDO_UID); only then
# is it marked as a safe directory, and fsmonitor hooks are always disabled, so
# a checkout prepared by another account cannot run code as root.

GIT_REPO_ROOT=''
GIT_COMMIT=''
GIT_INFRA_TREE=''
GIT_INFRA_DIRTY=false
GIT_SOURCE=checkout
RELEASE_TAG=''
PACKAGE_TREE=''

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
# Returns 1 outside a git checkout or below its root, 2 if the checkout has an
# untrusted owner.
git_state() {
  local real_infra
  real_infra=$(cd -- "$1" && pwd -P) || return 1
  GIT_REPO_ROOT=$(find_repo_root "$real_infra") || return 1
  [[ $GIT_REPO_ROOT == "$real_infra" ]] || return 1
  owner_allowed "$GIT_REPO_ROOT" || return 2
  git_in rev-parse --git-dir >/dev/null 2>&1 || return 1
  GIT_COMMIT=$(git_in rev-parse HEAD)
  GIT_INFRA_TREE=$(git_in rev-parse "HEAD^{tree}")
  if [[ -n $(git_in status --porcelain --untracked-files=normal) ]]; then
    GIT_INFRA_DIRTY=true
  else
    GIT_INFRA_DIRTY=false
  fi
}

# release_field NAME FILE: a top-level string of RELEASE.json, which has one
# key per line (empty for null or a missing key).
release_field() {
  sed -n "s/^  \"$1\": \"\([^\"\\\\]*\)\",\{0,1\}\$/\1/p" -- "$2"
}

# package_tree_hash DIR: the git tree hash of an unpacked release package's
# files, release/ left out.
package_tree_hash() {
  local dir=$1 tmp rc=0
  tmp=$(mktemp -d) || return 1
  (
    export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 HOME=$tmp LC_ALL=C
    base=(-c "safe.directory=$dir" -c core.fsmonitor=false --git-dir="$tmp/git" --work-tree="$dir")
    cd -- "$dir" \
      && git "${base[@]}" init -q \
      && mkdir -p "$tmp/git/info" \
      && printf '/release/\n' >"$tmp/git/info/exclude" \
      && git "${base[@]}" add -A \
      && git "${base[@]}" write-tree
  ) || rc=$?
  rm -rf -- "$tmp"
  return "$rc"
}

# package_state INFRA_DIR: set the GIT_* variables from an unpacked release
# package. Returns 1 when INFRA_DIR is no package (no release/RELEASE.json),
# 2 if it has an untrusted owner, 3 if RELEASE.json names no commit or tree,
# 4 if git cannot hash the files.
package_state() {
  local dir record tree commit
  dir=$(cd -- "$1" && pwd -P) || return 1
  record=$dir/release/RELEASE.json
  [[ -f $record ]] || return 1
  owner_allowed "$dir" || return 2
  commit=$(release_field commit "$record")
  tree=$(release_field infra_tree "$record")
  [[ $commit =~ ^[0-9a-f]{40}$ && $tree =~ ^[0-9a-f]{40}$ ]] || return 3
  PACKAGE_TREE=$(package_tree_hash "$dir") || return 4
  GIT_REPO_ROOT=$dir
  GIT_COMMIT=$commit
  GIT_INFRA_TREE=$tree
  GIT_SOURCE=package
  RELEASE_TAG=$(release_field tag "$record")
  if [[ $PACKAGE_TREE == "$tree" ]]; then
    GIT_INFRA_DIRTY=false
  else
    GIT_INFRA_DIRTY=true
  fi
}

# ref_matches_package REF: true if REF names the package's tag or commit.
ref_matches_package() {
  [[ -n $1 && ($1 == "$RELEASE_TAG" || $1 == "$GIT_COMMIT") ]]
}

# ref_matches_head REF: true if REF names the checked-out commit.
ref_matches_head() {
  local commit
  commit=$(git_in rev-parse --verify --quiet "$1^{commit}") || return 1
  [[ $commit == "$GIT_COMMIT" ]]
}
