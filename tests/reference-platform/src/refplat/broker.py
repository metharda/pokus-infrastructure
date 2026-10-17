"""refplat's inference broker ``broker`` (profile ``inference-broker``, BUILD_PROMPT §7.18).

The smallest service of its profile: it answers on the socket ``infer``
(researchers and root, through ``refplat infer``) and asks the inference API
that ``endpoints.toml`` names for the models it serves. The inference guard
lets exactly the platform's rendered controller and broker accounts reach
that API, so a distribution test that gets the model list through the broker
proves that the inference API answers the broker, not only that it refuses
every other account.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from refplat.common import RefplatError, endpoints, listen_sockets
from refplat.ctl import serve_socket

Fetch = Callable[[str, float], bytes]
TIMEOUT = 10.0
MAX_ANSWER = 1 << 20


def fetch(url: str, timeout: float = TIMEOUT) -> bytes:
    """GET a URL of the inference API, without any proxy of the environment.

    Args:
        url: An ``http`` URL below the inference API of ``endpoints.toml``.
        timeout: Seconds for connecting and answering.

    Returns:
        The body, at most ``MAX_ANSWER`` bytes and one more.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as answer:
        body: bytes = answer.read(MAX_ANSWER + 1)
        return body


class Broker:
    """The broker's answers."""

    def __init__(self, config: Mapping[str, Any], fetch_: Fetch = fetch) -> None:
        """Set up.

        Args:
            config: ``endpoints.toml``.
            fetch_: Reads a URL of the inference API.
        """
        inference = config.get("inference") or {}
        self.api_url = str(inference.get("api_url") or "").rstrip("/")
        self.fetch = fetch_

    def models(self) -> list[str]:
        """The ids of the models the inference API serves (its ``/v1/models``).

        Raises:
            RefplatError: If there is no inference API, it does not answer, or
                its answer is not a model list.
        """
        if not self.api_url.startswith(("http://", "https://")):
            raise RefplatError("endpoints.toml names no inference API")
        try:
            body = self.fetch(f"{self.api_url}/v1/models", TIMEOUT)
        except OSError as exc:
            raise RefplatError(f"the inference API does not answer: {exc}") from exc
        if len(body) > MAX_ANSWER:
            raise RefplatError("the inference API's answer is too long")
        try:
            data = json.loads(body)
        except ValueError:
            raise RefplatError("the inference API's answer is not JSON") from None
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise RefplatError("the inference API's answer has no model list")
        return sorted(str(item["id"]) for item in items if isinstance(item, dict) and "id" in item)

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Answer one request of ``infer``.

        Raises:
            RefplatError: On an unknown command or a failed request.
        """
        command = request.get("command")
        if command == "models":
            return {"ok": True, "api_url": self.api_url, "models": self.models()}
        raise RefplatError(f"unknown command {command!r}")


def main() -> int:
    """Run the broker on the socket systemd hands over."""
    sockets = listen_sockets()
    if "infer" not in sockets:
        sys.stderr.write("refplat-broker: no socket infer (socket activation)\n")
        return 1
    serve_socket(sockets["infer"], Broker(endpoints()).handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
