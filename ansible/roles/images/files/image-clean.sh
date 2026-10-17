#!/bin/sh
# Managed by pinka (ansible/roles/images/files/image-clean.sh). Local changes will be overwritten.
#
# Last step inside a build instance before it is stopped and published
# (BUILD_PROMPT §7.13): drop the package caches, shell histories, logs and
# cloud-init state, empty the machine-id and remove the SSH host keys and the
# host name; cloud-init sets the latter three again on the first boot of every
# instance launched from the image. Also removes the build's own files.
set -eu

apt-get clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin
for home in /root /home/*; do
  [ -d "$home" ] || continue
  rm -f "$home/.bash_history" "$home/.python_history" "$home/.lesshst" "$home/.viminfo" "$home/.wget-hsts"
  rm -rf "$home/.ansible" "$home/.cache"
done
journalctl --rotate >/dev/null 2>&1 || true
rm -rf /var/log/journal/*
find /var/log -type f -delete
cloud-init clean --logs --machine-id --seed
rm -f /etc/ssh/ssh_host_*
rm -f /etc/hostname
rm -rf /tmp/* /tmp/.[!.]* /var/tmp/* /var/tmp/.[!.]*
