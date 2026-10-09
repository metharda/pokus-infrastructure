#!/bin/sh
# Managed by pinka (images/_base/tests/test_base.sh). Local changes will be overwritten.
#
# Post-build assertions of `_base`, run inside the build instance.
set -eu
fail() {
  echo "test_base: $*" >&2
  exit 1
}

systemctl is-active --quiet otelcol-contrib.service || fail "the OpenTelemetry agent is not running"
otelcol-contrib validate --config=/etc/otelcol-contrib/config.yaml || fail "the agent configuration is invalid"
[ -f /run/otel-agent/resource.env ] || fail "the resource attribute helper did not run"
[ "$(readlink -f /etc/localtime)" = /usr/share/zoneinfo/Etc/UTC ] || fail "the time zone is not UTC"
for unit in apt-daily.timer apt-daily-upgrade.timer; do
  [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = masked ] || fail "$unit is not masked"
done
for tool in curl dig jq ip ss tcpdump strace lsof; do
  command -v "$tool" >/dev/null || fail "$tool is missing"
done
echo "test_base: ok"
