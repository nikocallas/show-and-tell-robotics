"""Tiny content-addressed disk cache for paid API responses (so reruns cost $0).

Entries live at ``settings.CACHE_DIR/<namespace>/<key>.{json,txt}``. A key is the sha256 of a
canonical JSON dump of the request (sorted keys; image data URLs included as-is). All writes are
atomic (temp file + ``os.replace``), so concurrent processes never see a half-written entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from showtell.config import settings

log = logging.getLogger(__name__)


def _default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, bytes):
        return {"__bytes_sha256__": hashlib.sha256(obj).hexdigest()}
    if isinstance(obj, set | frozenset):
        return sorted(obj, key=repr)
    raise TypeError(f"not JSON-serializable for a cache key: {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON text: sorted keys, no whitespace, unicode kept."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default
    )


def key_for(obj: Any) -> str:
    """sha256 hex digest of the canonical JSON of ``obj``."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """sha256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` atomically (creating parent folders)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def atomic_write_text(path: Path, text: str) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> Path:
    return atomic_write_text(
        path, json.dumps(value, indent=2, ensure_ascii=False, default=_default)
    )


def entry_path(namespace: str, key: str, suffix: str) -> Path:
    return Path(settings.CACHE_DIR) / namespace / f"{key}.{suffix}"


def get_json(namespace: str, key: str) -> Any | None:
    """Cached JSON value, or None on a miss (a corrupt entry counts as a miss)."""
    path = entry_path(namespace, key, "json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as err:
        log.warning("ignoring unreadable cache entry %s: %s", path, err)
        return None


def put_json(namespace: str, key: str, value: Any) -> Path:
    return atomic_write_json(entry_path(namespace, key, "json"), value)


def get_text(namespace: str, key: str) -> str | None:
    path = entry_path(namespace, key, "txt")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def put_text(namespace: str, key: str, text: str) -> Path:
    return atomic_write_text(entry_path(namespace, key, "txt"), text)
