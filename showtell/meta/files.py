"""Files API uploads (``purpose="user_data"``), deduplicated by content hash.

``cache/files.json`` maps ``{sha256: file_id}``. Uploads are synchronous (a 200 means the file is
ready) and uploaded files do not expire by default, so a cached id is reused across runs.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import openai

from showtell.config import settings
from showtell.meta.cache import atomic_write_json, file_sha256
from showtell.meta.client import client

log = logging.getLogger(__name__)
_LOCK = threading.Lock()
T = TypeVar("T")


def _index_path() -> Path:
    return Path(settings.CACHE_DIR) / "files.json"


def _load() -> dict[str, str]:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def cached_id(path: Path) -> str | None:
    """The cached file id for this file's content, if it was uploaded before."""
    return _load().get(file_sha256(path))


def upload(path: Path, *, force: bool = False) -> str:
    """Upload ``path`` once per content hash and return its ``file_id``."""
    path = Path(path)
    sha = file_sha256(path)
    with _LOCK:
        known = _load().get(sha)
    if known and not force:
        return known
    with path.open("rb") as f:
        obj = client().files.create(file=f, purpose="user_data")
    log.info("uploaded %s (%d bytes) -> %s", path.name, path.stat().st_size, obj.id)
    with _LOCK:
        index = _load()
        index[sha] = obj.id
        atomic_write_json(_index_path(), index)
    return obj.id


def invalidate(file_id: str) -> None:
    """Forget a cached id (e.g. after a 404 ``file_not_found``) so the next upload is fresh."""
    with _LOCK:
        index = _load()
        kept = {k: v for k, v in index.items() if v != file_id}
        if len(kept) != len(index):
            atomic_write_json(_index_path(), kept)


def is_missing_file(err: BaseException) -> bool:
    """True for a 404 ``file_not_found`` (deleted file, or an id from another team's key)."""
    return isinstance(err, openai.NotFoundError) and (
        getattr(err, "code", None) == "file_not_found" or "file" in str(err).lower()
    )


def with_file(path: Path, fn: Callable[[str], T]) -> T:
    """Run ``fn(file_id)`` for an uploaded ``path``; on ``file_not_found``, re-upload once."""
    file_id = upload(path)
    try:
        return fn(file_id)
    except openai.NotFoundError as err:
        if not is_missing_file(err):
            raise
        log.warning("file %s not found on the server; re-uploading %s", file_id, Path(path).name)
        invalidate(file_id)
        return fn(upload(path, force=True))
