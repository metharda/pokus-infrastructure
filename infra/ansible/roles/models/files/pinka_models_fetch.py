#!/opt/pinka/models-venv/bin/python3 -I
# Managed by pinka (ansible/roles/models/files/pinka_models_fetch.py). Local changes will be overwritten.
"""Download one pinned model or LoRA adapter of models.lock.yml and verify it.

Installed as ``/usr/local/libexec/pinka/models-fetch`` by the models role
(BUILD_PROMPT §7.12, contracts.md#paths). Files come from the pinned
``hf download`` CLI of ``/opt/pinka/models-venv``; every file's sha256 must
match the lock, files become 0444, and ``MANIFEST.json`` records what was
verified. An entry whose manifest matches the lock and whose files have the
recorded sizes is not touched again: verified files are never re-downloaded.
``--reverify`` (used by ``restore.yml``) hashes the files again instead and
downloads whatever is missing or changed.

Prints one JSON line: ``{"changed": bool, "path": "<directory>"}``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

CHUNK = 1 << 20
MANIFEST = "MANIFEST.json"
Runner = Callable[[Sequence[str], Mapping[str, str]], None]


class FetchError(RuntimeError):
    """The entry cannot be downloaded or does not match the lock."""


def target_dir(root: Path, kind: str, entry: Mapping[str, Any]) -> Path:
    """Directory of an entry (contracts.md#paths).

    Args:
        root: Models root (``/srv/pinka/models``).
        kind: ``model`` or ``adapter``.
        entry: Lock entry.

    Returns:
        ``<root>/<hf_repo>/<revision>`` for a model,
        ``<root>/adapters/<name>/<sha256 of the weights file>`` for an adapter.

    Raises:
        FetchError: If an adapter's weights file is not among its files.
    """
    if kind == "model":
        return root / str(entry["hf_repo"]) / str(entry["revision"])
    weights = [f["sha256"] for f in entry["files"] if f["path"] == entry["weights"]]
    if not weights:
        raise FetchError(f"adapter {entry['name']}: weights file {entry['weights']} is not listed")
    return root / "adapters" / str(entry["name"]) / str(weights[0])


def sha256_file(path: Path) -> str:
    """Hash a file.

    Args:
        path: File.

    Returns:
        Hex sha256.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_matches(dest: Path, entry: Mapping[str, Any]) -> bool:
    """Tell whether a directory already holds the verified entry.

    Args:
        dest: Entry directory.
        entry: Lock entry.

    Returns:
        True if MANIFEST.json lists exactly the lock's files and hashes and
        every file exists with the recorded size.
    """
    try:
        manifest = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    recorded = {f["path"]: f for f in manifest.get("files", [])}
    wanted = {f["path"]: f["sha256"] for f in entry["files"]}
    if {p: f.get("sha256") for p, f in recorded.items()} != wanted:
        return False
    if manifest.get("revision") != entry["revision"]:
        return False
    for path, record in recorded.items():
        file = dest / path
        if not file.is_file() or file.stat().st_size != record.get("size"):
            return False
    return True


def hashes_match(dest: Path, entry: Mapping[str, Any]) -> bool:
    """Hash every file of an entry again.

    Args:
        dest: Entry directory.
        entry: Lock entry.

    Returns:
        True if every pinned file exists with its pinned sha256.
    """
    for record in entry["files"]:
        file = dest / record["path"]
        if not file.is_file() or sha256_file(file) != record["sha256"]:
            return False
    return True


def _safe_path(dest: Path, relative: str) -> Path:
    path = (dest / relative).resolve()
    if not path.is_relative_to(dest.resolve()):
        raise FetchError(f"{relative} leaves the entry directory")
    return path


def _make_writable(dest: Path) -> None:
    for path in dest.rglob("*"):
        if path.is_file() and not path.is_symlink():
            path.chmod(0o644)
    if (dest / MANIFEST).exists():
        (dest / MANIFEST).unlink()


def fetch(
    entry: Mapping[str, Any],
    kind: str,
    root: Path,
    hf: str,
    run: Runner,
    *,
    owner: tuple[int, int] | None = None,
    tool_version: str = "",
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    token: str = "",
    reverify: bool = False,
) -> dict[str, Any]:
    """Download what is missing, verify every file and write the manifest.

    Args:
        entry: Lock entry (model or adapter).
        kind: ``model`` or ``adapter``.
        root: Models root.
        hf: Path of the ``hf`` CLI.
        run: Runs a command with extra environment variables and raises on failure.
        owner: ``(uid, gid)`` for the files and directories, or None to keep them.
        tool_version: huggingface_hub version recorded in the manifest.
        now: Clock (UTC).
        token: Hugging Face token for gated entries (passed as HF_TOKEN).
        reverify: Hash the files of a matching manifest again instead of trusting it.

    Returns:
        ``{"changed": bool, "path": str}``.

    Raises:
        FetchError: On a hash mismatch or a file outside the entry directory.
    """
    dest = target_dir(root, kind, entry)
    if manifest_matches(dest, entry) and (not reverify or hashes_match(dest, entry)):
        return {"changed": False, "path": str(dest)}
    dest.mkdir(parents=True, exist_ok=True)
    _make_writable(dest)
    files = {f["path"]: f["sha256"] for f in entry["files"]}
    missing = [
        path
        for path, digest in files.items()
        if not _safe_path(dest, path).is_file() or sha256_file(dest / path) != digest
    ]
    if missing:
        command = [hf, "download", entry["hf_repo"], *missing, "--revision", entry["revision"]]
        run([*command, "--local-dir", str(dest)], {"HF_TOKEN": token} if token else {})
    records = []
    for path, digest in files.items():
        file = _safe_path(dest, path)
        actual = sha256_file(file) if file.is_file() else "missing"
        if actual != digest:
            file.unlink(missing_ok=True)
            raise FetchError(
                f"{entry['hf_repo']}@{entry['revision']}: {path} is {actual}, pinned {digest}"
            )
        records.append({"path": path, "sha256": digest, "size": file.stat().st_size})
    shutil.rmtree(dest / ".cache", ignore_errors=True)  # hf download's own metadata
    manifest = {
        "schema_version": 1,
        "kind": kind,
        **{k: entry[k] for k in ("id", "name", "base_model_id", "weights") if k in entry},
        "hf_repo": entry["hf_repo"],
        "revision": entry["revision"],
        "license": entry["license"],
        "gated": entry["gated"],
        **({"quantization": entry["quantization"]} if kind == "model" else {}),
        "files": records,
        "downloaded_at": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": {"name": "huggingface_hub", "version": tool_version},
    }
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for path in sorted(dest.rglob("*"), reverse=True):
        path.chmod(0o755 if path.is_dir() else 0o444)
        if owner is not None:
            os.chown(path, *owner)
    dest.chmod(0o755)
    if owner is not None:
        os.chown(dest, *owner)
    return {"changed": True, "path": str(dest)}


def _run(argv: Sequence[str], env: Mapping[str, str]) -> None:
    environment = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"} | dict(env)
    subprocess.run(list(argv), check=True, env=environment)  # noqa: S603 - fixed program, arguments from the lock


def main(argv: Sequence[str]) -> int:
    """Fetch one entry given as JSON.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 on success, 1 on a verification or download failure.
    """
    parser = argparse.ArgumentParser(prog="models-fetch", description=__doc__.splitlines()[0])
    parser.add_argument("--kind", choices=["model", "adapter"], required=True)
    parser.add_argument("--entry", required=True, help="the lock entry as JSON")
    parser.add_argument("--root", type=Path, default=Path("/srv/pinka/models"))
    parser.add_argument("--hf", default="/opt/pinka/models-venv/bin/hf")
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    parser.add_argument("--tool-version", default="")
    parser.add_argument("--reverify", action="store_true", help="hash verified files again")
    token = parser.add_mutually_exclusive_group(required=True)
    token.add_argument("--token-stdin", action="store_true", help="read the token of a gated entry")
    token.add_argument("--no-token", action="store_true", help="public entry, no token")
    args = parser.parse_args(list(argv))
    hf_token = sys.stdin.readline().strip() if args.token_stdin else ""
    if args.token_stdin and not hf_token:
        sys.stderr.write("models-fetch: gated entry, but no Hugging Face token (vault_hf_token)\n")
        return 1
    owner = (args.uid, args.gid) if args.uid is not None and args.gid is not None else None
    try:
        result = fetch(
            json.loads(args.entry),
            args.kind,
            args.root,
            args.hf,
            _run,
            owner=owner,
            tool_version=args.tool_version,
            token=hf_token,
            reverify=args.reverify,
        )
    except (FetchError, subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"models-fetch: {exc}\n")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
