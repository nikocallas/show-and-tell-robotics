"""Bounded replays of streamed Meta Model API calls that fail after the stream opened.

The SDK's ``max_retries`` only covers the initial HTTP response (and honors ``Retry-After``
there). A failure after the stream opened arrives as a terminal ``error`` or ``response.failed``
event, or as a dropped connection, and the docs say to replay the full request for the transient
codes (https://dev.meta.ai/docs/error-handling#streaming-errors). Anything else is raised at once.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import openai

log = logging.getLogger(__name__)
T = TypeVar("T")
E = TypeVar("E", bound="CallError")

MAX_ATTEMPTS = 3
RETRYABLE_CODES = frozenset(
    {
        "server_shutting_down",
        "service_overloaded",
        "backend_unavailable",
        "server_error",
        "rate_limit_exceeded",
        "connection_error",  # the connection dropped or timed out mid-stream
        "stream_truncated",  # the stream closed without a terminal event
    }
)
# Retry-After the docs give for these 503 codes; a mid-stream event carries no header.
DOCUMENTED_WAIT_S = {"service_overloaded": 60.0, "backend_unavailable": 60.0}


class CallError(RuntimeError):
    """A Meta Model API call failed after the request was accepted.

    ``code`` is the API's machine-readable error code when known; ``usage`` is the usage the
    failing response reported, if any.
    """

    def __init__(self, message: str, code: str | None = None, *, usage: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.usage = usage

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


def _attr(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def from_event(cls: type[E], prefix: str, ev: Any) -> E:
    """``cls`` for a terminal ``error`` or ``response.failed`` event, keeping its code."""
    response = _attr(ev, "response")
    err = _attr(response, "error") if response is not None else ev
    nested = _attr(err, "error")
    code = _attr(err, "code") or _attr(nested, "code")
    message = _attr(err, "message") or _attr(nested, "message") or "no message"
    return cls(f"{prefix}: {message}", str(code) if code else None, usage=_attr(response, "usage"))


def from_sdk_error(cls: type[E], prefix: str, err: openai.APIError) -> E:
    """``cls`` for an SDK error raised while reading an open stream."""
    if isinstance(err, openai.APIConnectionError):  # also APITimeoutError
        return cls(f"{prefix}: connection lost mid-stream ({err})", "connection_error")
    return cls(f"{prefix}: {err.message}", err.code)


def backoff_s(attempt: int, code: str | None) -> float:
    """Seconds before replay number ``attempt`` (1, 2, ...): exponential with jitter, and at
    least the documented Retry-After for the code."""
    wait = min(30.0, 2.0 ** (attempt - 1)) + random.uniform(0.0, 1.0)
    return max(DOCUMENTED_WAIT_S.get(code or "", 0.0), wait)


def _should_replay(err: CallError, attempt: int, attempts: int, what: str) -> bool:
    if not err.retryable or attempt >= attempts:
        return False
    log.warning("%s failed (%s), replaying (%d/%d): %s", what, err.code, attempt + 1, attempts, err)
    return True


def call(fn: Callable[[], T], *, what: str, attempts: int = MAX_ATTEMPTS) -> T:
    """``fn()``, replayed after a retryable ``CallError`` (at most ``attempts`` runs in all)."""
    attempt = 1
    while True:
        try:
            return fn()
        except CallError as err:
            if not _should_replay(err, attempt, attempts, what):
                raise
            wait = backoff_s(attempt, err.code)
        time.sleep(wait)
        attempt += 1


async def acall(fn: Callable[[], Awaitable[T]], *, what: str, attempts: int = MAX_ATTEMPTS) -> T:
    """Async ``call``."""
    attempt = 1
    while True:
        try:
            return await fn()
        except CallError as err:
            if not _should_replay(err, attempt, attempts, what):
                raise
            wait = backoff_s(attempt, err.code)
        await asyncio.sleep(wait)
        attempt += 1
