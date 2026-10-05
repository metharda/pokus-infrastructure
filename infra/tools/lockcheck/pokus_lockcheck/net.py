"""Network access of the lock checker: downloads and OCI registry requests.

Registry checks use ``HEAD`` requests only, which Docker Hub does not count
against its anonymous pull limit. Authentication follows the standard bearer
token challenge (``WWW-Authenticate: Bearer realm=...,service=...,scope=...``),
so every registry that offers anonymous pulls works without configuration.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT = 120
CHUNK = 1 << 20
USER_AGENT = "pokus-lockcheck"
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
CHALLENGE_PARAM = re.compile(r'(\w+)="([^"]*)"')
DOCKER_HUB = "docker.io"
DOCKER_HUB_API = "registry-1.docker.io"
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT = 60


class FetchError(RuntimeError):
    """A download or registry request failed."""


def _open(request: urllib.request.Request) -> http.client.HTTPResponse:
    if not request.full_url.startswith("https://"):
        raise FetchError(f"refusing a non-https URL: {request.full_url}")
    request.add_header("User-Agent", USER_AGENT)
    try:
        response: http.client.HTTPResponse = urllib.request.urlopen(  # noqa: S310 - https only
            request, timeout=TIMEOUT
        )
        return response
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError(f"{request.full_url}: {exc}") from exc


def download(url: str) -> bytes:
    """Download a small file (keys, checksum lists).

    Args:
        url: https URL.

    Returns:
        The content.

    Raises:
        FetchError: On any error.
    """
    try:
        with _open(urllib.request.Request(url)) as response:  # noqa: S310 - https only
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url}: HTTP {exc.code}") from exc


def sha256_of_url(url: str) -> str:
    """Stream a download and return its sha256.

    Args:
        url: https URL.

    Returns:
        Hex digest.

    Raises:
        FetchError: On any error.
    """
    digest = hashlib.sha256()
    try:
        with _open(urllib.request.Request(url)) as response:  # noqa: S310 - https only
            while chunk := response.read(CHUNK):
                digest.update(chunk)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url}: HTTP {exc.code}") from exc
    return digest.hexdigest()


@dataclass(frozen=True)
class ImageRef:
    """A container image name split into registry and repository.

    Attributes:
        registry: Registry host as written (``docker.io``, ``ghcr.io``, ...).
        repository: Repository path (``library/postgres``).
    """

    registry: str
    repository: str

    @classmethod
    def parse(cls, image: str) -> ImageRef:
        """Split a fully qualified image name.

        Args:
            image: Name such as ``docker.io/library/postgres``.

        Returns:
            The reference.

        Raises:
            ValueError: If the name has no registry host.
        """
        host, _, repo = image.partition("/")
        if "." not in host or not repo:
            raise ValueError(f"image {image!r} must start with a registry host")
        return cls(host, repo)

    @property
    def api_host(self) -> str:
        """Host serving the registry API."""
        return DOCKER_HUB_API if self.registry == DOCKER_HUB else self.registry


def parse_challenge(header: str) -> dict[str, str]:
    """Parse a ``WWW-Authenticate: Bearer`` header.

    Args:
        header: Header value.

    Returns:
        Its parameters (``realm``, ``service``, ``scope``).

    Raises:
        FetchError: If it is not a bearer challenge.
    """
    if not header.lower().startswith("bearer "):
        raise FetchError(f"unsupported registry authentication: {header!r}")
    return dict(CHALLENGE_PARAM.findall(header))


def _token(challenge: dict[str, str], ref: ImageRef) -> str:
    params = {"scope": f"repository:{ref.repository}:pull"}
    if "service" in challenge:
        params["service"] = challenge["service"]
    url = challenge["realm"] + "?" + urllib.parse.urlencode(params)
    data = json.loads(download(url))
    token = data.get("token") or data.get("access_token")
    if not isinstance(token, str):
        raise FetchError(f"no token from {challenge['realm']}")
    return token


def manifest_digest(image: str, reference: str) -> str | None:
    """Resolve a tag or digest with a HEAD request.

    Args:
        image: Fully qualified image name.
        reference: Tag or ``sha256:`` digest.

    Returns:
        The digest the registry reports, or None if the reference does not exist.

    Raises:
        FetchError: On network or authentication errors.
    """
    ref = ImageRef.parse(image)
    url = f"https://{ref.api_host}/v2/{ref.repository}/manifests/{reference}"
    headers = {"Accept": MANIFEST_TYPES}
    rate_limited = 0
    while True:
        request = urllib.request.Request(url, headers=headers, method="HEAD")
        try:
            with _open(request) as response:
                return str(response.headers.get("Docker-Content-Digest") or "") or None
        except urllib.error.HTTPError as exc:
            if exc.code == HTTP_UNAUTHORIZED and "Authorization" not in headers:
                challenge = parse_challenge(exc.headers.get("WWW-Authenticate", ""))
                headers["Authorization"] = f"Bearer {_token(challenge, ref)}"
                continue
            if exc.code == HTTP_TOO_MANY_REQUESTS and rate_limited < RATE_LIMIT_RETRIES:
                rate_limited += 1
                time.sleep(retry_after(exc.headers.get("Retry-After"), rate_limited))
                continue
            if exc.code == HTTP_NOT_FOUND:
                return None
            raise FetchError(f"{url}: HTTP {exc.code}") from exc


def retry_after(header: str | None, attempt: int) -> float:
    """Seconds to wait after HTTP 429.

    Args:
        header: ``Retry-After`` value in seconds, if the registry sent one.
        attempt: Number of the retry (1, 2, ...), for exponential backoff.

    Returns:
        The wait, at most ``RATE_LIMIT_MAX_WAIT`` seconds.
    """
    try:
        wait = float(header) if header else 5.0 * 2 ** (attempt - 1)
    except ValueError:
        wait = 5.0 * 2 ** (attempt - 1)
    return max(0.0, min(wait, RATE_LIMIT_MAX_WAIT))
