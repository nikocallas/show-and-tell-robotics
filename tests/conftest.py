"""Every test runs on its own empty data and cache folders and without the developer's API key.

The suite never reads or writes the real data/ (usage log, jobs, database) or cache/ (normalized
videos, model responses), and nothing can reach the paid Meta API with the key from .env: tests
that need a key set a fake one. Module-scoped fixtures run before function-scoped ones, so a
session-wide fallback points them at a scratch folder too.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from showtell.config import settings


@pytest.fixture(scope="session", autouse=True)
def _session_isolation(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    base = tmp_path_factory.mktemp("settings")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", base / "cache")
        mp.setattr(settings, "SHOWTELL_DATA_DIR", base / "data")
        mp.setattr(settings, "MODEL_API_KEY", "")
        yield


@pytest.fixture(autouse=True)
def _isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MODEL_API_KEY", "")
