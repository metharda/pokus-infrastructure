#!/bin/sh
# Managed by pinka (bootstrap/lib/prepreflight.sh). Local changes will be overwritten.
#
# Minimal sanity checks before anything is installed:
# root, systemd as PID 1, glibc, a supported architecture, curl or wget (and
# tar, which unpacks the pinned uv), and a writable /opt. POSIX sh, no
# bashisms: it must work on any host before our tooling exists.
#
# Usage: prepreflight.sh [--interactive | --non-interactive] [--root DIR]
#   --interactive   may ask to enable systemd in /etc/wsl.conf on WSL
#   --root DIR      read files below DIR instead of / (fixture tests)
#
# Exit codes (the bootstrap codes): 0 ok, 20 unsupported, 30 needs action,
# 75 WSL restart required, 77 operator action (not root).
set -eu

ROOT=''
INTERACTIVE=false

while [ "$#" -gt 0 ]; do
  case $1 in
    --interactive) INTERACTIVE=true ;;
    --non-interactive) INTERACTIVE=false ;;
    --root)
      [ "$#" -ge 2 ] || {
        echo "prepreflight: --root needs a value" >&2
        exit 64
      }
      ROOT=$2
      shift
      ;;
    *)
      echo "prepreflight: unknown option: $1" >&2
      exit 64
      ;;
  esac
  shift
done

say() {
  printf '%s [prepreflight] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

stop() {
  code=$1
  shift
  for line in "$@"; do
    say "$line"
  done
  exit "$code"
}

# install_hint TOOL: the exact command that installs TOOL with this host's package manager.
install_hint() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "sudo apt-get update && sudo apt-get install -y $1"
  elif command -v dnf >/dev/null 2>&1; then
    echo "sudo dnf install -y $1"
  elif command -v zypper >/dev/null 2>&1; then
    echo "sudo zypper install -y $1"
  elif command -v pacman >/dev/null 2>&1; then
    echo "sudo pacman -S --needed $1"
  else
    echo "install $1 with the distribution's package manager"
  fi
}

# wsl_conf_systemd FILE: print the [boot] systemd value of a wsl.conf (lower case).
wsl_conf_systemd() {
  awk '
    /^[[:space:]]*\[/ { s = tolower($0); gsub(/[][[:space:]]/, "", s); section = s; next }
    section == "boot" {
      line = tolower($0)
      sub(/[#;].*/, "", line)
      if (match(line, /^[[:space:]]*systemd[[:space:]]*=/)) {
        value = substr(line, RLENGTH + 1)
        gsub(/[[:space:]]/, "", value)
      }
    }
    END { print value }
  ' "$1"
}

# wsl_conf_has_boot FILE: true if the file has a [boot] section.
wsl_conf_has_boot() {
  grep -qi '^[[:space:]]*\[boot\][[:space:]]*$' "$1"
}

check_root() {
  [ "$(id -u)" = 0 ] || stop 77 "bootstrap must run as root (use sudo)"
}

check_systemd() {
  pid1=$(cat "$ROOT/proc/1/comm" 2>/dev/null || true)
  if [ "$pid1" = systemd ]; then
    return 0
  fi
  osrelease=$(cat "$ROOT/proc/sys/kernel/osrelease" 2>/dev/null || true)
  case $osrelease in
    *[Mm]icrosoft*) ;;
    *) stop 20 "PID 1 is '${pid1:-unknown}', not systemd; pinka needs systemd as PID 1" \
      "Use a supported distribution with systemd, or run pinka in a VM with one." ;;
  esac
  if printf '%s\n' "$osrelease" | grep -Eqi '^4\.4\.0-[0-9]+-microsoft'; then
    stop 20 "WSL1 has no real Linux kernel and is unsupported." \
      "Convert the distribution on the Windows side: wsl --set-version <distro> 2"
  fi
  distro=${WSL_DISTRO_NAME:-<distro>}
  conf=$ROOT/etc/wsl.conf
  value=''
  if [ -f "$conf" ]; then
    value=$(wsl_conf_systemd "$conf")
  fi
  if [ "$value" = true ]; then
    stop 75 "systemd is enabled in /etc/wsl.conf but is not running yet." \
      "On the Windows side run: wsl --terminate $distro" \
      "Then open the distribution again and re-run bootstrap."
  fi
  if [ "$value" = '' ] && { [ ! -f "$conf" ] || ! wsl_conf_has_boot "$conf"; }; then
    if [ "$INTERACTIVE" = true ]; then
      printf 'systemd is not enabled in /etc/wsl.conf. Add "[boot] systemd=true" now? [y/N] ' >&2
      answer=''
      read -r answer || answer=''
      case $answer in
        [yY] | [yY][eE][sS])
          keep_original "$conf" /etc/wsl.conf
          if [ -f "$conf" ]; then
            printf '\n[boot]\nsystemd=true\n' >>"$conf"
          else
            printf '[boot]\nsystemd=true\n' >"$conf"
            chmod 0644 "$conf"
          fi
          stop 75 "systemd is now enabled in /etc/wsl.conf." \
            "On the Windows side run: wsl --terminate $distro" \
            "Then open the distribution again and re-run bootstrap."
          ;;
      esac
    fi
  fi
  stop 30 "systemd is not PID 1 on this WSL distribution." \
    "Set 'systemd=true' in the [boot] section of /etc/wsl.conf," \
    "then run on the Windows side: wsl --terminate $distro" \
    "and re-run bootstrap."
}

# keep_original FILE NAME: before the first change to a system file, which
# happens before the install record exists, keep its original below
# /var/lib/pinka/originals (NAME.absent when there was none), so the record
# and uninstall see it as it was.
keep_original() {
  copy=$ROOT/var/lib/pinka/originals$2
  if [ -e "$copy" ] || [ -e "$copy.absent" ]; then
    return 0
  fi
  mkdir -p "${copy%/*}"
  if [ -f "$1" ]; then
    cp -p "$1" "$copy"
  else
    : >"$copy.absent"
  fi
}

check_libc() {
  for f in "$ROOT"/lib/ld-musl-* "$ROOT"/usr/lib/ld-musl-*; do
    if [ -e "$f" ]; then
      stop 20 "this host uses musl libc; pinka needs glibc" \
        "Use a supported glibc distribution, or run pinka in a VM with one."
    fi
  done
  if command -v getconf >/dev/null 2>&1 && getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
    return 0
  fi
  if ldd --version 2>&1 | grep -Eqi 'glibc|gnu libc'; then
    return 0
  fi
  stop 20 "glibc was not found; pinka needs a glibc-based distribution"
}

check_arch() {
  arch=$(uname -m)
  case $arch in
    x86_64 | aarch64) ;;
    *) stop 20 "architecture '$arch' is unsupported (x86_64, or aarch64 as experimental)" ;;
  esac
}

check_tools() {
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    stop 30 "neither curl nor wget is installed; install one, then re-run bootstrap:" \
      "  $(install_hint curl)"
  fi
  if ! command -v tar >/dev/null 2>&1; then
    stop 30 "tar is not installed; install it, then re-run bootstrap:" "  $(install_hint tar)"
  fi
}

check_opt() {
  target=$ROOT/opt/pinka
  [ -d "$target" ] || target=$ROOT/opt
  [ -d "$target" ] || target=$ROOT/
  [ -w "$target" ] || stop 30 "$target is not writable; pinka installs its tooling under /opt/pinka" \
    "Make /opt writable (for example remount it read-write), then re-run bootstrap."
}

check_root
check_systemd
check_libc
check_arch
check_tools
check_opt
say "ok: root, systemd, glibc, $(uname -m), download tool, writable /opt"
