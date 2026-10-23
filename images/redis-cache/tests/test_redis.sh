#!/bin/sh
# Managed by pinka (images/redis-cache/tests/test_redis.sh). Local changes will be overwritten.
#
# Post-build assertions of `redis-cache`, run inside the build instance.
set -eu
fail() {
  echo "test_redis: $*" >&2
  exit 1
}

systemctl is-active --quiet redis-server.service || fail "Redis is not running"
[ "$(redis-cli ping)" = PONG ] || fail "Redis does not answer PING"
redis-cli set build-check ok >/dev/null && [ "$(redis-cli get build-check)" = ok ] || fail "Redis cannot store a value"
redis-cli del build-check >/dev/null
echo "test_redis: ok"
