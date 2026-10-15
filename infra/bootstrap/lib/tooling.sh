# shellcheck shell=bash
# shellcheck disable=SC2034 # paths are read by the scripts that source this file
# Managed by pinka (bootstrap/lib/tooling.sh). Local changes will be overwritten.
#
# Pinned tooling under /opt/pinka (BUILD_PROMPT §5.2 steps 2, 4-7; §19.3):
# uv (checksum-verified), CPython installed by uv, the hash-locked Ansible
# environment, the pinned Ansible collections, and the check that generated
# version files match versions.lock.yml. Every function is idempotent.

readonly PINKA_OPT=/opt/pinka
readonly PINKA_BIN=$PINKA_OPT/bin
readonly PINKA_PYTHON_DIR=$PINKA_OPT/python
readonly PINKA_PYTHON=$PINKA_PYTHON_DIR/bin/python3
readonly ANSIBLE_VENV=$PINKA_OPT/ansible-venv
readonly ANSIBLE_COLLECTIONS=$PINKA_OPT/ansible-collections
readonly INSTALLER_VENV=$PINKA_OPT/installer-venv
readonly UV_CACHE=/var/cache/pinka/uv
readonly SHA256_RE='^[0-9a-f]{64}$'

# uv_env: the environment every uv call runs with (no user or system uv config).
uv_env() {
  env UV_NO_CONFIG=1 UV_CACHE_DIR="$UV_CACHE" UV_PYTHON_INSTALL_DIR="$PINKA_PYTHON_DIR" \
    UV_PYTHON_BIN_DIR="$PINKA_PYTHON_DIR/bin" UV_NO_PROGRESS=1 "$PINKA_BIN/uv" "$@"
}

# download URL FILE: fetch an https URL with curl or wget.
download() {
  local url=$1 file=$2
  if [[ $url != https://* ]]; then
    log_error "refusing to download a non-https URL: $url"
    return 1
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$file" "$url"
  else
    wget -q --https-only -O "$file" "$url"
  fi
}

# verify_sha256 FILE SHA256: true if the file has the expected digest.
verify_sha256() {
  local file=$1 expected=$2 actual
  if ! [[ $expected =~ $SHA256_RE ]]; then
    log_error "no verified sha256 for $(basename -- "$file") in versions.lock.yml (value: '$expected')"
    return 1
  fi
  actual=$(sha256sum -- "$file" | cut -d' ' -f1)
  if [[ $actual != "$expected" ]]; then
    log_error "checksum mismatch for $(basename -- "$file"): expected $expected, got $actual"
    return 1
  fi
}

# uv_asset ARCH: print "<url> <sha256>" of the pinned uv archive for ARCH.
uv_asset() {
  case $1 in
    x86_64) printf '%s %s\n' "$PINKA_UV_URL_X86_64" "$PINKA_UV_SHA256_X86_64" ;;
    aarch64) printf '%s %s\n' "$PINKA_UV_URL_AARCH64" "$PINKA_UV_SHA256_AARCH64" ;;
    *) return 1 ;;
  esac
}

# install_uv [DIR]: install the pinned uv into DIR, by default /opt/pinka/bin
# (make setup: build/tools); skips a matching install.
install_uv() {
  local dir=${1:-$PINKA_BIN} arch asset url sha tmp found current=''
  if [[ -x $dir/uv ]]; then
    current=$("$dir/uv" --version 2>/dev/null) || current=''
  fi
  if [[ $current == "uv $PINKA_UV_VERSION" || $current == "uv $PINKA_UV_VERSION "* ]]; then
    log_info "uv $PINKA_UV_VERSION already installed"
    return 0
  fi
  arch=$(uname -m)
  if ! asset=$(uv_asset "$arch"); then
    log_error "no pinned uv build for architecture $arch"
    return "$EXIT_UNSUPPORTED"
  fi
  read -r url sha <<<"$asset"
  tmp=$(mktemp -d)
  if ! download "$url" "$tmp/uv.tar.gz" || ! verify_sha256 "$tmp/uv.tar.gz" "$sha"; then
    rm -rf -- "$tmp"
    return "$EXIT_TOOLING"
  fi
  if ! tar -xzf "$tmp/uv.tar.gz" -C "$tmp"; then
    log_error "cannot unpack the uv archive"
    rm -rf -- "$tmp"
    return "$EXIT_TOOLING"
  fi
  install -d -m 0755 "$dir"
  for found in uv uvx; do
    local path
    path=$(find "$tmp" -type f -name "$found" -perm -u+x | head -n 1)
    if [[ -z $path ]]; then
      log_error "the uv archive does not contain '$found'"
      rm -rf -- "$tmp"
      return "$EXIT_TOOLING"
    fi
    install -m 0755 "$path" "$dir/$found"
  done
  rm -rf -- "$tmp"
  log_info "installed uv $PINKA_UV_VERSION to $dir"
}

# install_python: install the pinned CPython with uv and link /opt/pinka/python/bin/python3.
install_python() {
  local minor=${PINKA_PYTHON_VERSION%.*}
  install -d -m 0755 "$PINKA_PYTHON_DIR" "$UV_CACHE"
  if ! uv_env python install "$PINKA_PYTHON_VERSION"; then
    log_error "uv could not install CPython $PINKA_PYTHON_VERSION"
    return "$EXIT_TOOLING"
  fi
  ln -sfn "python$minor" "$PINKA_PYTHON"
  if [[ $("$PINKA_PYTHON" --version 2>&1) != "Python $PINKA_PYTHON_VERSION" ]]; then
    log_error "$PINKA_PYTHON is not Python $PINKA_PYTHON_VERSION"
    return "$EXIT_TOOLING"
  fi
  log_info "CPython $PINKA_PYTHON_VERSION ready at $PINKA_PYTHON"
}

# setup_venv VENV LOCKFILE: create VENV with the pinned CPython and sync it to LOCKFILE.
setup_venv() {
  local venv=$1 lock=$2
  if [[ ! -x $venv/bin/python ]] || [[ $("$venv/bin/python" --version 2>&1) != "Python $PINKA_PYTHON_VERSION" ]]; then
    rm -rf -- "$venv"
    if ! uv_env venv --python "$PINKA_PYTHON" "$venv"; then
      log_error "uv could not create $venv"
      return "$EXIT_TOOLING"
    fi
  fi
  if ! uv_env pip sync --python "$venv/bin/python" --require-hashes "$lock"; then
    log_error "uv could not install $(basename -- "$lock") into $venv"
    return "$EXIT_TOOLING"
  fi
}

# install_collections REQUIREMENTS: install pinned Ansible collections, if any are listed.
install_collections() {
  local requirements=$1 count
  if ! count=$("$ANSIBLE_VENV/bin/python" -c '
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as handle:
    data = yaml.safe_load(handle) or {}
print(len(data.get("collections") or []))
' "$requirements"); then
    log_error "cannot read $requirements"
    return "$EXIT_TOOLING"
  fi
  if [[ $count == 0 ]]; then
    log_info "no Ansible collections are pinned yet"
    return 0
  fi
  install -d -m 0755 "$ANSIBLE_COLLECTIONS"
  if ! "$ANSIBLE_VENV/bin/ansible-galaxy" collection install -r "$requirements" -p "$ANSIBLE_COLLECTIONS"; then
    log_error "ansible-galaxy could not install the collections in $requirements"
    return "$EXIT_TOOLING"
  fi
}

# lock_digest FILE: print the sha256 of FILE.
lock_digest() {
  sha256sum -- "$1" | cut -d' ' -f1
}

# check_versions_sync LOCK VERSIONS_YML: generated files must match versions.lock.yml.
check_versions_sync() {
  local lock=$1 versions=$2 digest recorded
  digest=$(lock_digest "$lock")
  recorded=$(sed -n 's/^pinka_versions_lock_sha256: *"\{0,1\}\([0-9a-f]\{64\}\)"\{0,1\} *$/\1/p' "$versions")
  if [[ $recorded != "$digest" || $PINKA_LOCK_SHA256 != "$digest" ]]; then
    log_error "generated version files are out of date with versions.lock.yml; run 'make generate' and commit"
    return 1
  fi
}
