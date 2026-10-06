#!/bin/sh
# Managed by pokus-infra (tests/distros/containers/prepare.sh). Local changes will be overwritten.
#
# Installs systemd, Python 3 (3.11 on openSUSE Leap, whose python3 is too old
# for Ansible) and sudo in a CI fast-lane container. Distribution packages are
# not pinned: the container exists for one CI job only.
set -eu
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends systemd systemd-sysv dbus python3 sudo
  rm -rf /var/lib/apt/lists/*
elif command -v dnf >/dev/null; then
  dnf install -y systemd python3 sudo procps-ng
  dnf clean all
elif command -v zypper >/dev/null; then
  zypper --non-interactive install systemd python311 sudo
  zypper clean --all
elif command -v pacman >/dev/null; then
  pacman -Syu --noconfirm systemd python sudo
  pacman -Scc --noconfirm
else
  echo "prepare.sh: no known package manager" >&2
  exit 1
fi
