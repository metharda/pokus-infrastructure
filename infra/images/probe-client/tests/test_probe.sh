#!/bin/sh
# Managed by pinka (images/probe-client/tests/test_probe.sh). Local changes will be overwritten.
#
# Post-build assertions of `probe-client`, run inside the build instance.
set -eu
fail() {
  echo "test_probe: $*" >&2
  exit 1
}

for tool in curl dig ab psql redis-cli nc; do
  command -v "$tool" >/dev/null || fail "$tool is missing"
done
ab -V >/dev/null || fail "ab does not run"
echo "test_probe: ok"
