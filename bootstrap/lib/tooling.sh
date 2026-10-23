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
# download: attempts in all, and the seconds before the second and the third.
readonly DOWNLOAD_TRIES=3
readonly -a DOWNLOAD_DELAYS=(5 15)
# curl exit codes of a transport error: no proxy or host name, no connection,
# HTTP/2 framing, a partial file, a timeout, a failed TLS handshake, an empty
# reply, a failed send or receive, an HTTP/2 stream error. Not 60: a refused
# certificate is not retried.
readonly CURL_TRANSIENT=' 5 6 7 16 18 28 35 52 55 56 92 '

# uv_env: the environment every uv call runs with (no user or system uv config).
uv_env() {
  env UV_NO_CONFIG=1 UV_CACHE_DIR="$UV_CACHE" UV_PYTHON_INSTALL_DIR="$PINKA_PYTHON_DIR" \
    UV_PYTHON_BIN_DIR="$PINKA_PYTHON_DIR/bin" UV_NO_PROGRESS=1 "$PINKA_BIN/uv" "$@"
}

# transient_status CODE: true for an HTTP status worth another attempt
# (408, 429, 5xx).
transient_status() {
  [[ $1 == 408 || $1 == 429 || $1 == 5[0-9][0-9] ]]
}

# download_tool: the download command, curl or wget.
download_tool() {
  if command -v curl >/dev/null 2>&1; then
    echo curl
  else
    echo wget
  fi
}

# fetch_once URL FILE: one attempt. Returns 0 on success; on failure prints
# why and returns 2 for a transport error, 1 for any other.
fetch_once() {
  local url=$1 file=$2 rc=0 code status
  if [[ $(download_tool) == curl ]]; then
    code=$(curl -sSL --proto '=https' --tlsv1.2 -o "$file" -w '%{http_code}' "$url") || rc=$?
    if ((rc != 0)); then
      echo "curl exit $rc"
      [[ $CURL_TRANSIENT == *" $rc "* ]] && return 2
      return 1
    fi
    [[ $code == 2[0-9][0-9] ]] && return 0
    echo "HTTP $code"
  else
    status=$(wget -q -S --https-only -O "$file" "$url" 2>&1) || rc=$?
    ((rc == 0)) && return 0
    code=$(sed -n 's/^ *HTTP\/[0-9.]* \([0-9][0-9][0-9]\).*/\1/p' <<<"$status" | tail -n 1)
    echo "wget exit $rc${code:+, HTTP $code}"
    # 4: network failure; 8: an error response from the server.
    ((rc == 4)) && return 2
    ((rc == 8)) || return 1
  fi
  transient_status "$code" && return 2
  return 1
}

# download URL FILE: fetch an https URL with curl or wget. A transport error
# (no connection, a timeout, a connection that broke off, HTTP 408, 429 or
# 5xx) is tried again, up to DOWNLOAD_TRIES attempts with the increasing
# DOWNLOAD_DELAYS, and every attempt is logged; any other failure (another
# HTTP status, a refused certificate) stops at once. The caller checks the
# content: a checksum mismatch is never retried.
download() {
  local url=$1 file=$2 attempt rc why delay
  if [[ $url != https://* ]]; then
    log_error "refusing to download a non-https URL: $url"
    return 1
  fi
  for ((attempt = 1; ; attempt++)); do
    log_info "downloading $url (attempt $attempt of $DOWNLOAD_TRIES)"
    rc=0
    why=$(fetch_once "$url" "$file") || rc=$?
    ((rc == 0)) && return 0
    if ((rc != 2)); then
      log_error "cannot download $url: $why (attempt $attempt of $DOWNLOAD_TRIES; not retried)"
      return 1
    fi
    if ((attempt >= DOWNLOAD_TRIES)); then
      log_error "cannot download $url: $why (attempt $attempt of $DOWNLOAD_TRIES; none left)"
      return 1
    fi
    delay=${DOWNLOAD_DELAYS[attempt - 1]}
    log_warn "download of $url failed: $why (attempt $attempt of $DOWNLOAD_TRIES); next attempt in $delay s"
    sleep "$delay"
  done
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

# collection_pins: print "<name> <version> <url> <sha256>" of every pinned
# collection (ansible_collections of versions.lock.yml, through pins.sh).
collection_pins() {
  local pin
  for pin in "${PINKA_COLLECTIONS[@]}"; do
    printf '%s\n' "$pin"
  done
}

# ansible_galaxy ARGS...: the Ansible environment's ansible-galaxy. No
# Ansible command of bootstrap writes bytecode: a __pycache__ directory inside
# a collection fails its verify, which would reinstall it on every run.
ansible_galaxy() {
  PYTHONDONTWRITEBYTECODE=1 "$ANSIBLE_VENV/bin/ansible-galaxy" "$@"
}

# check_collection_artifact NAME VERSION FILE: the artifact passes the check
# make lock-collections and make setup apply (MANIFEST.json and FILES.json,
# and only relative symbolic links that stay inside the collection), run with
# the Ansible environment's Python, isolated and standard library only.
check_collection_artifact() {
  "$ANSIBLE_VENV/bin/python" -I "$INFRA_DIR/tools/lockcheck/pinka_lockcheck/artifact.py" "$@" </dev/null
}

# install_collections DEST: the pinned Ansible collections in DEST, exactly
# the artifacts of versions.lock.yml. Nothing happens when the installed ones
# already verify (ansible-galaxy collection verify --offline: every file
# against its FILES.json). Otherwise every artifact is taken from the release
# package's release/collections/ when bootstrap runs from one (GIT_SOURCE
# package), or downloaded from Galaxy, checked against its pinned sha256 and
# with check_collection_artifact before anything is installed, then installed
# without contacting Galaxy and without resolving dependencies (transitive
# dependencies are pinned too), and verified again.
install_collections() {
  local dest=$1 work pin name version url sha256 file rc=0 bundled=''
  [[ ${GIT_SOURCE:-checkout} == package ]] && bundled=$INFRA_DIR/release/collections
  local -a pins=() names=() files=()
  mapfile -t pins < <(collection_pins)
  if ((${#pins[@]} == 0)); then
    log_info "no Ansible collections are pinned yet"
    return 0
  fi
  for pin in "${pins[@]}"; do
    read -r name version url sha256 <<<"$pin"
    names+=("$name:$version")
  done
  install -d -m 0755 "$dest" || return "$EXIT_TOOLING"
  if ansible_galaxy collection verify --offline -p "$dest" "${names[@]}" >/dev/null 2>&1 </dev/null; then
    log_info "the pinned Ansible collections are installed and verify"
    return 0
  fi
  work=$(mktemp -d) || return "$EXIT_TOOLING"
  for pin in "${pins[@]}"; do
    read -r name version url sha256 <<<"$pin"
    if [[ -n $bundled ]]; then
      file="$bundled/${name/./-}-$version.tar.gz"
      if [[ ! -f $file ]]; then
        log_error "the release package has no artifact of collection $name $version ($file)"
        rc=$EXIT_TOOLING
        break
      fi
    else
      file="$work/${name/./-}-$version.tar.gz"
      if ! download "$url" "$file"; then
        log_error "cannot download collection $name $version from '$url' (ansible_collections.artifacts in versions.lock.yml)"
        rc=$EXIT_TOOLING
        break
      fi
    fi
    if ! verify_sha256 "$file" "$sha256"; then
      rc=$EXIT_TOOLING
      break
    fi
    if ! check_collection_artifact "$name" "$version" "$file"; then
      log_error "the artifact of collection $name $version is refused (reason above); nothing is installed"
      rc=$EXIT_TOOLING
      break
    fi
    files+=("$file")
  done
  if ((rc == 0)) && ! ansible_galaxy collection install --offline --no-deps --force -p "$dest" "${files[@]}" </dev/null; then
    log_error "ansible-galaxy could not install the pinned collections"
    rc=$EXIT_TOOLING
  fi
  rm -rf -- "$work"
  if ((rc == 0)) && ! ansible_galaxy collection verify --offline -p "$dest" "${names[@]}" </dev/null; then
    log_error "the installed collections do not match their FILES.json"
    rc=$EXIT_TOOLING
  fi
  return "$rc"
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
