"""The suite never touches the real data/ and cache/ folders or the API key (tests/conftest.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from showtell.config import ROOT, settings

REAL = (ROOT / "data", ROOT / "cache")


def _scratch(folder: Path) -> bool:
    return not any(folder.resolve().is_relative_to(real.resolve()) for real in REAL)


@pytest.fixture(scope="module")
def module_folders() -> tuple[Path, Path, str]:
    """What a module-scoped fixture sees: it runs before any function-scoped fixture."""
    return settings.CACHE_DIR, settings.SHOWTELL_DATA_DIR, settings.MODEL_API_KEY


def test_each_test_gets_its_own_data_and_cache_folders(tmp_path: Path) -> None:
    folders = (settings.CACHE_DIR, settings.SHOWTELL_DATA_DIR, settings.usage_log)
    assert folders == (tmp_path / "cache", tmp_path / "data", tmp_path / "data" / "usage.jsonl")
    assert settings.MODEL_API_KEY == ""


def test_module_fixtures_get_scratch_folders_too(module_folders: tuple[Path, Path, str]) -> None:
    cache, data, key = module_folders
    assert _scratch(cache) and _scratch(data) and key == ""
