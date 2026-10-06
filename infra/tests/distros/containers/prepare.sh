#!/bin/sh
# Managed by pokus-infra (tests/distros/containers/prepare.sh). Local changes will be overwritten.
#
# Installs systemd, Python 3 (3.11 on openSUSE Leap, whose python3 is too old
# for Ansible), sudo and procps (sysctl; every real host has it) in a CI
# fast-lane container. Distribution packages are not pinned: the container
# exists for one CI job only.
#
# Also the Python bindings Ansible's package modules need (python3-apt,
# python3-libdnf5 with dnf5): a real run installs them on demand, check mode
# cannot. The package metadata stays in the image because apt does not
# refresh it in check mode.
set -eu
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends systemd systemd-sysv dbus python3 python3-apt sudo procps
elif command -v dnf >/dev/null; then
  set -- systemd python3 sudo procps-ng
  if command -v dnf5 >/dev/null; then
    set -- "$@" python3-libdnf5
  fi
  dnf install -y "$@"
elif command -v zypper >/dev/null; then
  zypper --non-interactive install systemd python311 sudo procps
elif command -v pacman >/dev/null; then
  pacman -Syu --noconfirm systemd python sudo
else
  echo "prepare.sh: no known package manager" >&2
  exit 1
fi
