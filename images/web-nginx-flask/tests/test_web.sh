#!/bin/sh
# Managed by pinka (images/web-nginx-flask/tests/test_web.sh). Local changes will be overwritten.
#
# Post-build assertions of `web-nginx-flask`, run inside the build instance.
set -eu
fail() {
  echo "test_web: $*" >&2
  exit 1
}

for unit in nginx.service webapp.service; do
  systemctl is-active --quiet "$unit" || fail "$unit is not running"
done
nginx -t 2>/dev/null || fail "the nginx configuration is invalid"
tries=0
until curl -fsS http://127.0.0.1/health >/dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -lt 20 ] || fail "/health does not answer through nginx"
  sleep 1
done
[ "$(curl -fsS http://127.0.0.1/health | jq -r .status)" = ok ] || fail "/health is not ok"
curl -fsS http://127.0.0.1/ | grep -q "hello" || fail "the page is wrong"
echo "test_web: ok"
