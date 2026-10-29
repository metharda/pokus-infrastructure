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
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO

TIMEOUT = 120
CHUNK = 1 << 20
USER_AGENT = "pinka-lockcheck"
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
# Google's pull-through mirror of Docker Hub, read for sizes before Docker Hub
# (anonymous reads are limited per address), and ECR Public's copies of
# Docker Hub's official images.
DOCKER_HUB_MIRROR = "mirror.gcr.io"
ECR_PUBLIC = "public.ecr.aws"
ECR_DOCKER_LIBRARY = "docker/library/"
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT = 60


HTTP_REQUEST_TIMEOUT = 408
TRANSIENT_STATUSES = (HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS)


class FetchError(RuntimeError):
    """A download or registry request failed.

    Attributes:
        transient: Whether it is a transport error that another attempt may
            not meet (no connection, a timeout, a connection that broke off,
            HTTP 408, 429 or 5xx; :mod:`pinka_lockcheck.retry`). A refused
            certificate, another HTTP status or a refused URL is not.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        """A failed request; ``transient`` as described above."""
        super().__init__(message)
        self.transient = transient


def transient_status(code: int) -> bool:
    """Whether an HTTP status is worth another attempt (408, 429 or 5xx)."""
    return code in TRANSIENT_STATUSES or code >= HTTP_SERVER_ERROR


def _transport_error(url: str, exc: BaseException) -> FetchError:
    """A connection error, transient unless the server's certificate was refused."""
    reason = getattr(exc, "reason", exc)
    refused = isinstance(reason, ssl.SSLCertVerificationError)
    return FetchError(f"{url}: {exc}", transient=not refused)


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
        raise _transport_error(request.full_url, exc) from exc


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
        raise FetchError(f"{url}: HTTP {exc.code}", transient=transient_status(exc.code)) from exc
    except (OSError, http.client.HTTPException) as exc:  # broke off while reading
        raise _transport_error(url, exc) from exc


def sha256_of_url(url: str, out: BinaryIO | None = None) -> str:
    """Stream a download and return its sha256.

    Args:
        url: https URL.
        out: File that receives the content, if given.

    Returns:
        Hex digest.

    Raises:
        FetchError: On any error.
    """
    digest = hashlib.sha256()
    try:
        with _open(urllib.request.Request(url)) as response:  # noqa: S310 - https only
            while chunk := _read(url, response):
                digest.update(chunk)
                if out is not None:
                    out.write(chunk)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url}: HTTP {exc.code}", transient=transient_status(exc.code)) from exc
    return digest.hexdigest()


def _read(url: str, response: http.client.HTTPResponse) -> bytes:
    """The next chunk; a connection that breaks off is a transport error."""
    try:
        return response.read(CHUNK)
    except (OSError, http.client.HTTPException) as exc:
        raise _transport_error(url, exc) from exc


def reachable(url: str, timeout: float = 15.0) -> bool:
    """Whether an https server answers at all (any status below 500).

    Args:
        url: https URL.
        timeout: Seconds to wait.

    Returns:
        False when the connection fails (no route, refused, a proxy that
        refuses the tunnel, a timeout) or the server answers with 5xx.
    """
    if not url.startswith("https://"):
        return False
    headers = {"User-Agent": USER_AGENT}
    request = urllib.request.Request(url, method="HEAD", headers=headers)  # noqa: S310 - https
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310 - https only
            return True
    except urllib.error.HTTPError as exc:
        return exc.code < HTTP_SERVER_ERROR
    except (urllib.error.URLError, OSError):
        return False


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


def registry_json(image: str, path: str) -> tuple[dict[str, str], Any]:
    """GET a JSON document of a registry's v2 API (a tag list, a manifest).

    Args:
        image: Fully qualified image name.
        path: Path below the repository (``tags/list``, ``manifests/<tag>``).

    Returns:
        The response headers (names in lower case) and the decoded JSON.

    Raises:
        FetchError: On network, authentication or decoding errors.
    """
    ref = ImageRef.parse(image)
    url = f"https://{ref.api_host}/v2/{ref.repository}/{path}"
    headers = {"Accept": MANIFEST_TYPES}
    rate_limited = 0
    while True:
        request = urllib.request.Request(url, headers=headers)
        try:
            with _open(request) as response:
                headers_found = {k.lower(): v for k, v in response.headers.items()}
                return headers_found, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == HTTP_UNAUTHORIZED and "Authorization" not in headers:
                challenge = parse_challenge(exc.headers.get("WWW-Authenticate", ""))
                headers["Authorization"] = f"Bearer {_token(challenge, ref)}"
                continue
            if exc.code == HTTP_TOO_MANY_REQUESTS and rate_limited < RATE_LIMIT_RETRIES:
                rate_limited += 1
                time.sleep(retry_after(exc.headers.get("Retry-After"), rate_limited))
                continue
            raise FetchError(
                f"{url}: HTTP {exc.code}", transient=transient_status(exc.code)
            ) from exc
        except ValueError as exc:
            raise FetchError(f"{url}: not JSON") from exc


# The platforms the lock pins images for (versions-lock.schema.json, $defs.image).
LOCK_PLATFORMS = ("linux/amd64", "linux/arm64")
RegistryJson = Callable[[str, str], "tuple[Mapping[str, str], Any]"]


def platform_of(entry: Mapping[str, Any]) -> str:
    """``os/architecture`` of an index entry (``unknown/unknown`` for attestations)."""
    found = entry.get("platform") or {}
    return f"{found.get('os')}/{found.get('architecture')}"


def size_sources(image: str) -> list[str]:
    """Where to read an image's manifests, in order: names that hold the same digests.

    Docker Hub limits anonymous manifest reads per address, so its images are
    read from Google's mirror first; ECR Public's copies of Docker Hub's
    official images fall back to the mirror.
    """
    ref = ImageRef.parse(image)
    if ref.registry == DOCKER_HUB:
        return [f"{DOCKER_HUB_MIRROR}/{ref.repository}", image]
    if ref.registry == ECR_PUBLIC and ref.repository.startswith(ECR_DOCKER_LIBRARY):
        return [image, f"{DOCKER_HUB_MIRROR}/{ref.repository.removeprefix('docker/')}"]
    return [image]


def image_sizes(image: str, digest: str, fetch: RegistryJson = registry_json) -> dict[str, int]:
    """The compressed size of a pinned image per platform: the sum of its layers' sizes.

    The requirements computation estimates the unpacked size from it until the
    image is pulled on a host. A digest names its
    content, so every source of :func:`size_sources` that holds it gives the
    same sizes; the next is read when one fails.

    Args:
        image: Fully qualified image name.
        digest: The digest of its multi-platform index.
        fetch: ``(image, path) -> (headers, JSON)`` from the registry's v2 API.

    Returns:
        ``{platform: bytes}`` for every platform of the lock the index holds.

    Raises:
        FetchError: If no source answers, the digest is not a multi-platform
            index, or a manifest has no layers.
    """
    sources = size_sources(image)
    for number, source in enumerate(sources, 1):
        try:
            return _sizes_from(source, digest, fetch)
        except FetchError:
            if number == len(sources):
                raise
    raise FetchError(f"{image}: no source")  # pragma: no cover - sources is never empty


def _sizes_from(image: str, digest: str, fetch: RegistryJson) -> dict[str, int]:
    _, index = fetch(image, f"manifests/{digest}")
    entries = index.get("manifests") if isinstance(index, dict) else None
    if not entries:
        raise FetchError(f"{image}@{digest} is not a multi-platform index")
    children: dict[str, str] = {}
    for entry in entries:
        platform = platform_of(entry)
        if platform in LOCK_PLATFORMS:
            children.setdefault(platform, str(entry["digest"]))
    sizes: dict[str, int] = {}
    for platform, child in sorted(children.items()):
        _, manifest = fetch(image, f"manifests/{child}")
        layers = manifest.get("layers") if isinstance(manifest, dict) else None
        if not layers:
            raise FetchError(f"{image}@{child} ({platform}) has no layers")
        sizes[platform] = sum(int(layer["size"]) for layer in layers)
    return sizes


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
