"""The models role: lock and catalog, the hash-locked tool environment, models-fetch."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pinka_models_fetch as fetch
import pytest

from ansible_testlib import INFRA, ROLES, load_yaml

REVISION = "1" * 40
FILES = {"config.json": b"{}", "model.safetensors": b"weights", "sub/tokenizer.json": b"tok"}
NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


def entry(files: Mapping[str, bytes] = FILES, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "test-model",
        "hf_repo": "example-org/test-model",
        "revision": REVISION,
        "files": [{"path": p, "sha256": hashlib.sha256(d).hexdigest()} for p, d in files.items()],
        "license": "apache-2.0",
        "gated": False,
        "quantization": "awq",
        "notes": "",
    }
    data.update(changes)
    return data


class FakeHub:
    """Stands in for `hf download`: writes the requested files into --local-dir."""

    def __init__(self, content: Mapping[str, bytes] = FILES) -> None:
        self.content = dict(content)
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        self.calls.append((list(argv), dict(env)))
        local = Path(argv[argv.index("--local-dir") + 1])
        names = argv[3 : argv.index("--revision")]
        (local / ".cache" / "huggingface").mkdir(parents=True, exist_ok=True)
        for name in names:
            (local / name).parent.mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(self.content[name])


def run_fetch(
    root: Path, hub: FakeHub, data: dict[str, Any], kind: str = "model", **kw: Any
) -> dict[str, Any]:
    return fetch.fetch(
        data,
        kind,
        root,
        "/opt/pinka/models-venv/bin/hf",
        hub,
        now=lambda: NOW,
        tool_version="2.1.1",
        **kw,
    )


def test_download_verify_and_manifest(tmp_path: Path) -> None:
    hub = FakeHub()
    result = run_fetch(tmp_path, hub, entry())
    dest = tmp_path / "example-org" / "test-model" / REVISION
    assert result == {"changed": True, "path": str(dest)}
    argv, env = hub.calls[0]
    assert argv[:4] == [
        "/opt/pinka/models-venv/bin/hf",
        "download",
        "example-org/test-model",
        "config.json",
    ]
    assert argv[-4:] == ["--revision", REVISION, "--local-dir", str(dest)]
    assert env == {}
    assert not (dest / ".cache").exists()
    manifest = json.loads((dest / "MANIFEST.json").read_text())
    assert manifest["id"] == "test-model"
    assert manifest["downloaded_at"] == "2026-10-06T12:00:00Z"
    assert manifest["tool"] == {"name": "huggingface_hub", "version": "2.1.1"}
    assert {f["path"]: f["size"] for f in manifest["files"]} == {
        p: len(d) for p, d in FILES.items()
    }
    for path in dest.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o755 if path.is_dir() else 0o444), path


def test_verified_entries_are_never_downloaded_again(tmp_path: Path) -> None:
    hub = FakeHub()
    run_fetch(tmp_path, hub, entry())
    assert run_fetch(tmp_path, hub, entry())["changed"] is False
    assert len(hub.calls) == 1


def test_only_missing_files_are_downloaded(tmp_path: Path) -> None:
    hub = FakeHub()
    run_fetch(tmp_path, hub, entry())
    dest = tmp_path / "example-org" / "test-model" / REVISION
    (dest / "config.json").chmod(0o644)
    (dest / "config.json").unlink()
    assert run_fetch(tmp_path, hub, entry())["changed"] is True
    assert hub.calls[1][0][3:4] == ["config.json"]
    assert hub.calls[1][0][4] == "--revision"


def test_reverify_hashes_again_and_repairs_changed_files(tmp_path: Path) -> None:
    hub = FakeHub()
    run_fetch(tmp_path, hub, entry())
    dest = tmp_path / "example-org" / "test-model" / REVISION
    name = next(iter(FILES))
    (dest / name).chmod(0o644)
    (dest / name).write_bytes(bytes(len(FILES[name])))  # same size, other content
    assert run_fetch(tmp_path, hub, entry())["changed"] is False  # the manifest is trusted
    assert run_fetch(tmp_path, hub, entry(), reverify=True)["changed"] is True
    assert (dest / name).read_bytes() == FILES[name]
    assert hub.calls[-1][0][3:4] == [name]
    assert run_fetch(tmp_path, hub, entry(), reverify=True)["changed"] is False
    assert len(hub.calls) == 2


def test_hash_mismatch_fails_and_removes_the_file(tmp_path: Path) -> None:
    hub = FakeHub({**FILES, "model.safetensors": b"tampered"})
    with pytest.raises(fetch.FetchError, match=r"model\.safetensors is"):
        run_fetch(tmp_path, hub, entry())
    dest = tmp_path / "example-org" / "test-model" / REVISION
    assert not (dest / "model.safetensors").exists()
    assert not (dest / "MANIFEST.json").exists()


def test_paths_cannot_leave_the_entry(tmp_path: Path) -> None:
    with pytest.raises(fetch.FetchError, match="leaves the entry directory"):
        run_fetch(tmp_path, FakeHub(), entry({"../escape": b"x"}))


def test_adapters_and_tokens(tmp_path: Path) -> None:
    files = {"adapter_config.json": b"{}", "adapter_model.safetensors": b"lora"}
    adapter = entry(files, gated=True)
    del adapter["id"], adapter["quantization"]
    adapter.update(name="style", weights="adapter_model.safetensors", base_model_id="test-model")
    hub = FakeHub(files)
    result = run_fetch(tmp_path, hub, adapter, kind="adapter", token="hf_test")  # noqa: S106 - fake token
    weights = hashlib.sha256(b"lora").hexdigest()
    assert result["path"] == str(tmp_path / "adapters" / "style" / weights)
    assert hub.calls[0][1] == {"HF_TOKEN": "hf_test"}
    manifest = json.loads((Path(result["path"]) / "MANIFEST.json").read_text())
    assert (manifest["kind"], manifest["base_model_id"]) == ("adapter", "test-model")
    adapter["weights"] = "missing.safetensors"
    with pytest.raises(fetch.FetchError, match="weights file"):
        run_fetch(tmp_path, hub, adapter, kind="adapter")


def test_main_requires_a_token_for_gated_entries(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("\n"))
    code = fetch.main(
        ["--kind", "model", "--entry", json.dumps(entry(gated=True)), "--token-stdin"]
    )
    assert code == 1
    assert "no Hugging Face token" in capsys.readouterr().err


def test_lock_and_catalog() -> None:
    lock = load_yaml(INFRA / "models.lock.yml")
    assert lock == {"schema_version": 1, "models": [], "adapters": []}
    catalog = load_yaml(INFRA / "models.catalog.yml")
    assert catalog["status"] == "proposed"  # owner answer E7: not final without approval
    permissive = {"apache-2.0", "mit"}
    assert 3 <= len(catalog["models"]) <= 5
    for model in catalog["models"]:
        assert model["license"] in permissive and not model["gated"], model["id"]
        assert model["status"] == "proposed"
        assert "ada-12g" in model["profiles"]
    for model in catalog["restricted"]:
        assert model["license"] not in permissive or model["gated"], model["id"]
    profile = load_yaml(INFRA / "vllm/profiles/ada-12g.yml")
    for model in catalog["models"] + catalog["restricted"]:
        assert model["quantization"] in profile["quantizations"], model["id"]


def test_requirements_lock_is_hash_locked() -> None:
    text = (ROLES / "models" / "files" / "requirements.lock").read_text()
    lock = load_yaml(INFRA / "versions.lock.yml")
    pinned = lock["python"]["model_tools"]["huggingface-hub"]
    assert f"huggingface-hub=={pinned} " in text
    requirements = [line for line in text.splitlines() if line and line[0].isalnum()]
    hashes = [line for line in text.splitlines() if "--hash=sha256:" in line]
    assert requirements and len(hashes) >= len(requirements)
