#!/bin/sh
# Managed by pinka (images/docker-host/tests/test_docker.sh). Local changes will be overwritten.
#
# Post-build assertions of `docker-host`, run inside the build instance.
set -eu
fail() {
  echo "test_docker: $*" >&2
  exit 1
}

systemctl is-active --quiet docker.service || fail "Docker is not running"
docker info >/dev/null 2>&1 || fail "docker info fails (nesting?)"
systemctl is-active --quiet web-container.service || fail "the web service is not running"
tries=0
until curl -fsS http://127.0.0.1:8080/ >/dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -lt 30 ] || fail "the containerized web service does not answer on 8080"
  sleep 1
done
echo "test_docker: ok"
