"""Settings loaded from `.env` (git-ignored) and the process environment.

Import this module before `mujoco` so `MUJOCO_GL` is set before the GL backend is chosen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

_VALID_GL = {"darwin": {"cgl", "glfw"}, "linux": {"egl", "osmesa", "glfw"}}


def _gl_backend() -> str:
    requested = os.environ.get("MUJOCO_GL", "").strip().lower()
    valid = _VALID_GL.get(sys.platform, {"glfw"})
    if requested in valid:
        return requested
    return "cgl" if sys.platform == "darwin" else "egl"


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


class Settings(BaseModel):
    MODEL_API_KEY: str = ""
    META_BASE_URL: str = "https://api.meta.ai/v1"
    WONDER_MODEL: str = "muse-wonder-1.0"
    SAM_MODEL: str = "sam-3.1"
    MUJOCO_GL: str = "egl"
    SHOWTELL_DATA_DIR: Path = ROOT / "data"
    CACHE_DIR: Path = ROOT / "cache"
    MENAGERIE_DIR: Path = ROOT / "third_party" / "mujoco_menagerie"
    # Published prices (USD), see https://dev.meta.ai/docs/pricing-rate-limits
    WONDER_PRICE_IN_PER_M: float = 4.00
    WONDER_PRICE_CACHED_IN_PER_M: float = 0.20
    WONDER_PRICE_OUT_PER_M: float = 20.00
    SAM_PRICE_PER_FRAME: float = 0.20 / 1000
    SAM_PRICE_PER_IMAGE: float = 2.50 / 1000
    SAM_MIN_FRAMES_PER_REQUEST: int = 50
    BUDGET_WARN_USD: float = 100.0

    @property
    def has_api_key(self) -> bool:
        return bool(self.MODEL_API_KEY.strip())

    @property
    def usage_log(self) -> Path:
        return self.SHOWTELL_DATA_DIR / "usage.jsonl"


def load_settings() -> Settings:
    env = os.environ
    s = Settings(
        MODEL_API_KEY=env.get("MODEL_API_KEY", ""),
        META_BASE_URL=env.get("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/"),
        WONDER_MODEL=env.get("WONDER_MODEL", "muse-wonder-1.0"),
        SAM_MODEL=env.get("SAM_MODEL", "sam-3.1"),
        MUJOCO_GL=_gl_backend(),
        SHOWTELL_DATA_DIR=_resolve(env.get("SHOWTELL_DATA_DIR", "./data")),
        CACHE_DIR=_resolve(env.get("SHOWTELL_CACHE_DIR", "./cache")),
        SAM_PRICE_PER_FRAME=float(env.get("SAM_PRICE_PER_FRAME", 0.20 / 1000)),
    )
    os.environ["MUJOCO_GL"] = s.MUJOCO_GL
    return s


settings = load_settings()
