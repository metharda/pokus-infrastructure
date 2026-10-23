#!/bin/sh
# Hands OTEL_RESOURCE_ATTRIBUTES to the OpenTelemetry agent. systemd does not
# pass its own environment to services, so the value that Incus gives to the
# instance's init process (environment.* configuration of a container) is
# copied into /run/otel-agent/resource.env. A VM has no such variable on its
# init process; there the platform writes /etc/otel-agent/resource.env at
# launch, which is used when init has none. Only a conservative character set
# is accepted.
set -eu
umask 022
out=/run/otel-agent/resource.env
mkdir -p /run/otel-agent
value=$(tr '\0' '\n' </proc/1/environ | sed -n 's/^OTEL_RESOURCE_ATTRIBUTES=//p' | head -n 1)
if [ -z "$value" ] && [ -r /etc/otel-agent/resource.env ]; then
  value=$(sed -n 's/^OTEL_RESOURCE_ATTRIBUTES=//p' /etc/otel-agent/resource.env | head -n 1)
fi
case $value in
  *[!A-Za-z0-9._,=:/-]*)
    echo "resource-env: OTEL_RESOURCE_ATTRIBUTES has unexpected characters; ignored" >&2
    value=
    ;;
esac
printf 'OTEL_RESOURCE_ATTRIBUTES=%s\n' "$value" >"$out.tmp"
mv "$out.tmp" "$out"
