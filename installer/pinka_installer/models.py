"""Pinning a model in ``models.lock.yml`` (BUILD_PROMPT §18.4 screen 9, docs/how-to/add-a-model.md).

The TUI resolves an entry of models.catalog.yml or a custom repository to a full commit and
the sha256 of every file vLLM needs, from the Hugging Face Hub's API: the tree
of the commit gives the sha256 of files stored in Git LFS (the weights); the
small files are downloaded at the pinned commit and hashed. Every file's size
goes into the lock too, for the requirements computed from the choices
(BUILD_PROMPT §2.3). Nothing is pinned
from an unverified value. The network calls go through an injectable getter.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from pinka_installer import validators, yamlio

HUB = "https://huggingface.co"
TIMEOUT = 30
# Files vLLM needs: weights, configuration, tokenizer and chat template.
NEEDED = (
    "*.safetensors",
    "*.json",
    "*.jinja",
    "tokenizer.model",
    "*.tiktoken",
    "merges.txt",
    "vocab.txt",
)
SKIPPED = ("README*", ".gitattributes", "*.md", "*.png", "*.jpg", "*.gif")
# TODO(verify): the Hub API field names (revision "sha", tree "path", "type", "size" and
# "lfs.oid" as the sha256 of an LFS object, "lfs.size" as its size); the Hub was not
# reachable from the build environment.
LFS_SHA256 = ("lfs", "oid")
LFS_SIZE = ("lfs", "size")
SIZE = "size"

Getter = Callable[[str, str], bytes]


class HubError(RuntimeError):
    """The Hub refused a request or answered something unusable."""


def hub_get(url: str, token: str) -> bytes:
    """GET a Hub URL, with the token for gated repositories.

    Args:
        url: The URL.
        token: Hugging Face token, or empty.

    Returns:
        The body.

    Raises:
        HubError: For HTTP and network errors.
    """
    request = urllib.request.Request(url)  # noqa: S310 - https only, see HUB
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            body: bytes = response.read()
            return body
    except OSError as exc:
        raise HubError(f"{url}: {exc}") from exc


def _json(get: Getter, path: str, token: str) -> Any:  # noqa: ANN401 - JSON
    try:
        return json.loads(get(f"{HUB}{path}", token))
    except ValueError as exc:
        raise HubError(f"{path}: not JSON") from exc


def whoami(token: str, get: Getter = hub_get) -> str:
    """Check a token (the Secrets of the GPU screen).

    Args:
        token: Hugging Face token.
        get: HTTP getter.

    Returns:
        The account name.

    Raises:
        HubError: If the token is not accepted.
    """
    data = _json(get, "/api/whoami-v2", token)
    name = data.get("name") if isinstance(data, dict) else None
    if not name:
        raise HubError("the Hub did not accept the token")
    return str(name)


def resolve_revision(repo: str, ref: str, token: str, get: Getter = hub_get) -> str:
    """The full commit of a branch, tag or commit of a model repository.

    Args:
        repo: ``<owner>/<name>``.
        ref: Branch, tag or commit.
        token: Hugging Face token, or empty.
        get: HTTP getter.

    Returns:
        The 40-hex commit.

    Raises:
        HubError: If the Hub gives no commit.
    """
    quoted = urllib.parse.quote(ref, safe="")
    data = _json(get, f"/api/models/{repo}/revision/{quoted}", token)
    sha = str(data.get("sha", "")) if isinstance(data, dict) else ""
    if validators.revision(sha):
        raise HubError(f"{repo}@{ref}: no commit in the Hub's answer")
    return sha


def needed(path: str) -> bool:
    """Whether vLLM needs a file of the repository.

    Args:
        path: Path in the repository.

    Returns:
        True for weights, configuration, tokenizer and chat template files.
    """
    name = path.rsplit("/", 1)[-1]
    if any(fnmatch.fnmatch(name, p) for p in SKIPPED):
        return False
    return any(fnmatch.fnmatch(name, p) for p in NEEDED)


@dataclass(frozen=True)
class PinnedFile:
    """A file of a pinned model.

    Attributes:
        path: Path in the repository.
        sha256: Its sha256.
        size: Its size in bytes.
    """

    path: str
    sha256: str
    size: int


def pin_files(repo: str, revision: str, token: str, get: Getter = hub_get) -> list[PinnedFile]:
    """The sha256 of every file vLLM needs at a commit.

    Args:
        repo: ``<owner>/<name>``.
        revision: The 40-hex commit.
        token: Hugging Face token, or empty.
        get: HTTP getter.

    Returns:
        The files, sorted by path.

    Raises:
        HubError: If the tree is unusable or has no weights.
    """
    tree = _json(get, f"/api/models/{repo}/tree/{revision}?recursive=1", token)
    if not isinstance(tree, list):
        raise HubError(f"{repo}@{revision}: no file tree")
    files = []
    for entry in tree:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        path = str(entry.get("path", ""))
        if not needed(path):
            continue
        lfs = entry.get(LFS_SHA256[0])
        digest = str(lfs.get(LFS_SHA256[1], "")) if isinstance(lfs, dict) else ""
        size = lfs.get(LFS_SIZE[1]) if isinstance(lfs, dict) else entry.get(SIZE)
        if not digest:
            quoted = urllib.parse.quote(path)
            body = get(f"{HUB}/{repo}/resolve/{revision}/{quoted}", token)
            digest = hashlib.sha256(body).hexdigest()
            size = len(body)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise HubError(f"{repo}@{revision}: no size for {path}")
        files.append(PinnedFile(path, digest, size))
    if not any(f.path.endswith(".safetensors") for f in files):
        raise HubError(f"{repo}@{revision}: no .safetensors weights")
    return sorted(files, key=lambda f: f.path)


def lock_entry(
    model_id: str,
    repo: str,
    revision: str,
    files: list[PinnedFile],
    meta: Mapping[str, Any],
) -> dict[str, Any]:
    """A ``models.lock.yml`` entry.

    Args:
        model_id: The id vLLM serves it under.
        repo: ``<owner>/<name>``.
        revision: The 40-hex commit.
        files: The pinned files.
        meta: license, gated, quantization and notes (the listed model's entry or the
            operator's input).

    Returns:
        The entry.
    """
    entry: dict[str, Any] = {
        "id": model_id,
        "hf_repo": repo,
        "revision": revision,
        "files": [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in files],
        "license": str(meta.get("license", "")),
        "gated": bool(meta.get("gated", False)),
    }
    if meta.get("quantization"):
        entry["quantization"] = str(meta["quantization"])
    if meta.get("notes"):
        entry["notes"] = str(meta["notes"])
    return entry


def add_to_lock(text: str, entry: Mapping[str, Any]) -> str:
    """Add or replace a model in ``models.lock.yml``, keeping its comments.

    Args:
        text: The lock.
        entry: From :func:`lock_entry`.

    Returns:
        The new lock.
    """
    yaml = yamlio.round_trip()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    doc = yaml.load(text)
    models = doc.get("models")
    if not isinstance(models, CommentedSeq):
        models = CommentedSeq()
        doc["models"] = models
    node = CommentedMap(entry)
    node["files"] = CommentedSeq()
    for item in entry["files"]:
        file_node = CommentedMap(item)
        file_node.fa.set_flow_style()
        node["files"].append(file_node)
    index = next((i for i, m in enumerate(models) if m.get("id") == entry["id"]), None)
    if index is None:
        models.append(node)
    else:
        models[index] = node
    models.fa.set_block_style()
    stream = io.StringIO()
    yaml.dump(doc, stream)
    return stream.getvalue()
