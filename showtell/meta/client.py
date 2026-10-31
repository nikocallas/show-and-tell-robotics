"""OpenAI SDK clients pointed at the Meta Model API (OpenAI-compatible, Bearer auth)."""

from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from showtell.config import settings


class MissingAPIKeyError(RuntimeError):
    """MODEL_API_KEY is not set (create one at https://dev.meta.ai and add it to .env)."""

    def __init__(self) -> None:
        super().__init__("MODEL_API_KEY missing: add it to .env (see .env.example)")


def _api_key() -> str:
    key = settings.MODEL_API_KEY.strip()
    if not key:
        raise MissingAPIKeyError()
    return key


def client() -> OpenAI:
    return OpenAI(base_url=settings.META_BASE_URL, api_key=_api_key(), timeout=300, max_retries=3)


def aclient() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.META_BASE_URL, api_key=_api_key(), timeout=300, max_retries=3
    )
