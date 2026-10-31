"""Files API upload dedup by content hash (fake client) and the missing-key error."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from showtell.config import settings
from showtell.meta import client as client_mod
from showtell.meta import files


class FakeFiles:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, *, file: Any, purpose: str) -> SimpleNamespace:
        self.calls.append({"name": Path(file.name).name, "purpose": purpose, "data": file.read()})
        return SimpleNamespace(id=f"file-{len(self.calls)}", object="file", status="uploaded")


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeFiles:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key-not-real")
    f = FakeFiles()
    monkeypatch.setattr(files, "client", lambda: SimpleNamespace(files=f))
    return f


def test_upload_is_cached_by_content(fake: FakeFiles, tmp_path: Path) -> None:
    a = tmp_path / "a.mp4"
    a.write_bytes(b"video-bytes")
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(b"video-bytes")
    assert files.upload(a) == "file-1"
    assert files.upload(copy) == "file-1"  # same content, no second upload
    assert fake.calls == [{"name": "a.mp4", "purpose": "user_data", "data": b"video-bytes"}]
    index = json.loads((settings.CACHE_DIR / "files.json").read_text())
    assert list(index.values()) == ["file-1"]
    assert files.cached_id(copy) == "file-1"


def test_invalidate_and_force(fake: FakeFiles, tmp_path: Path) -> None:
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    files.upload(a)
    files.invalidate("file-1")
    assert files.cached_id(a) is None
    assert files.upload(a) == "file-2"
    assert files.upload(a, force=True) == "file-3"


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MODEL_API_KEY", "  ")
    with pytest.raises(client_mod.MissingAPIKeyError, match="MODEL_API_KEY missing"):
        client_mod.client()
    with pytest.raises(RuntimeError):
        client_mod.aclient()


def not_found(code: str, message: str) -> openai.NotFoundError:
    body = {"message": message, "type": "invalid_request_error", "param": None, "code": code}
    response = SimpleNamespace(request=None, status_code=404, headers={})
    return openai.NotFoundError(f"Error code: 404 - {message}", response=response, body=body)


def test_with_file_reuploads_once_on_file_not_found(fake: FakeFiles, tmp_path: Path) -> None:
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    seen: list[str] = []

    def use(file_id: str) -> str:
        seen.append(file_id)
        if file_id == "file-1":
            raise not_found("file_not_found", "File not found")
        return "ok"

    assert files.with_file(a, use) == "ok"
    assert seen == ["file-1", "file-2"] and files.cached_id(a) == "file-2"

    def wrong_model(file_id: str) -> str:
        raise not_found("model_not_found", "The requested model was not found.")

    with pytest.raises(openai.NotFoundError):  # other 404s propagate without a re-upload
        files.with_file(a, wrong_model)
    assert files.cached_id(a) == "file-2"
