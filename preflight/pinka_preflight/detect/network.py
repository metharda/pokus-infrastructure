"""Network reachability of the endpoints a lab install needs.

The endpoint list comes from ``endpoints.yml`` (compiled to JSON). Each
endpoint applies to the detected distribution through optional ``when``
conditions. Every applicable endpoint gets a DNS lookup and an HTTPS request
with a short timeout; any HTTP answer below 500 counts as reachable. Optional
endpoints never fail preflight; unreachable required endpoints make the
outcome ``needs_action``. ``--offline`` skips all probes.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pinka_preflight.runner import Runner


@dataclass
class EndpointResult:
    """Reachability of one endpoint.

    Attributes:
        id: Endpoint identifier.
        url: Probed URL.
        host: Host name.
        purpose: Why the lab needs it.
        required: Unreachable means ``needs_action``.
        status: ``ok``, ``dns_failed``, ``unreachable`` or ``skipped``.
        http_status: HTTP status, if a response arrived.
        detail: Error text, if any.
    """

    id: str
    url: str
    host: str
    purpose: str
    required: bool
    status: str
    http_status: int | None = None
    detail: str | None = None


def applies(when: Mapping[str, object], context: Mapping[str, object]) -> bool:
    """Evaluate an endpoint's ``when`` conditions.

    Supported keys: ``families`` and ``distros`` (lists the detected value must
    be in) and ``gpu_present`` (boolean).

    Args:
        when: Conditions; empty means "always".
        context: ``family``, ``distro`` and ``gpu_present`` of the host.

    Returns:
        True if every condition holds.
    """
    for key, wanted in when.items():
        if key == "families" and isinstance(wanted, list):
            if context.get("family") not in wanted:
                return False
        elif key == "distros" and isinstance(wanted, list):
            if context.get("distro") not in wanted:
                return False
        elif key == "gpu_present":
            if bool(context.get("gpu_present")) != bool(wanted):
                return False
        else:
            raise ValueError(f"unknown endpoint condition {key!r}")
    return True


def check_endpoints(
    runner: Runner,
    endpoints: Sequence[Mapping[str, object]],
    context: Mapping[str, object],
    *,
    offline: bool,
    timeout: float = 5.0,
) -> list[EndpointResult]:
    """Probe every applicable endpoint.

    Args:
        runner: Command and network runner.
        endpoints: Endpoint definitions from ``endpoints.json``.
        context: Host context for the ``when`` conditions.
        offline: Skip all probes.
        timeout: Seconds per DNS lookup and per HTTPS request.

    Returns:
        One result per applicable endpoint.
    """
    results: list[EndpointResult] = []
    for ep in endpoints:
        when = ep.get("when") or {}
        if not isinstance(when, dict) or not applies(when, context):
            continue
        url = str(ep["url"])
        host = urllib.parse.urlsplit(url).hostname or ""
        result = EndpointResult(
            id=str(ep["id"]),
            url=url,
            host=host,
            purpose=str(ep.get("purpose", "")),
            required=bool(ep.get("required", False)),
            status="skipped",
        )
        if not offline:
            if runner.resolve(host, timeout) is None:
                result.status, result.detail = "dns_failed", f"{host} does not resolve"
            else:
                probe = runner.https_probe(url, timeout)
                result.http_status = probe.status
                if probe.reachable:
                    result.status = "ok"
                else:
                    result.status = "unreachable"
                    result.detail = probe.error or f"HTTP {probe.status}"
        results.append(result)
    return results


def summarize(results: Sequence[EndpointResult]) -> dict[str, int]:
    """Count results by status.

    Args:
        results: Endpoint results.

    Returns:
        Status to count.
    """
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
