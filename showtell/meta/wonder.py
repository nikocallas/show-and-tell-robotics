"""Muse Wonder calls: JSON in, validated JSON out, with one retry, a disk cache and usage logging.

Every call streams (``stream=True``) and reads the terminal ``response.completed`` event, because
non-streaming requests can hit the server-side time limit on long reasoning (HTTP 504
``gateway_timeout``, https://dev.meta.ai/docs/error-handling#504) while streamed ones cannot.
A transient mid-stream failure (``server_shutting_down``, a dropped connection, ...) replays the
whole request a bounded number of times (``showtell.meta.retry``). Structured output uses the
Responses ``text.format`` json_schema; if the server rejects it, the call falls back to
prompt-only JSON for that schema for the rest of the process.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import cv2
import numpy as np
import openai
from pydantic import BaseModel

from showtell.config import settings
from showtell.meta import cache, retry
from showtell.meta.client import client
from showtell.meta.usage import COUNT_FIELDS, log_usage, usage_counts

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")
Content = list[dict[str, Any]]

EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")  # "none" is a 400 on Wonder
_NO_SCHEMA: set[str] = set()  # schema names whose json_schema format the server rejected


class WonderError(retry.CallError):
    """A Wonder call failed (stream error, empty reply, ...); ``code`` is the API's error code."""


class WonderJSONError(WonderError):
    """No valid JSON after the retry. ``outputs`` holds the raw replies."""

    def __init__(self, message: str, outputs: list[str]) -> None:
        super().__init__(message)
        self.outputs = outputs


# ---------------------------------------------------------------- content parts


def text_part(text: str) -> dict[str, Any]:
    return {"type": "input_text", "text": text}


def image_part(image: Path | str | bytes, detail: str = "auto") -> dict[str, Any]:
    """An ``input_image`` part as a JPEG data URL (non-JPEG input is re-encoded).

    A ``str`` that already is a ``data:`` or ``http(s)`` URL is passed through.
    """
    if isinstance(image, str) and image.startswith(("data:", "http://", "https://")):
        return {"type": "input_image", "image_url": image, "detail": detail}
    data = image if isinstance(image, bytes) else Path(image).read_bytes()
    if not data.startswith(b"\xff\xd8\xff"):
        arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError("image_part: could not decode image bytes")
        ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError("image_part: JPEG encoding failed")
        data = buf.tobytes()
    url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": url, "detail": detail}


def video_file_part(file_id: str, fps: float | None = None) -> dict[str, Any]:
    """An ``input_video`` part referencing a Files API upload."""
    part: dict[str, Any] = {"type": "input_video", "file_id": file_id}
    if fps is not None:
        part["fps"] = fps
    return part


def video_url_part(url: str, fps: float | None = None) -> dict[str, Any]:
    """An ``input_video`` part from a public URL or a base64 data URL (50 MB inline limit)."""
    part: dict[str, Any] = {"type": "input_video", "video_url": url}
    if fps is not None:
        part["fps"] = fps
    return part


def file_part(file_id: str) -> dict[str, Any]:
    """An ``input_file`` part (the docs' recommended form for uploaded videos on Muse models)."""
    return {"type": "input_file", "file_id": file_id}


def video_data_url(path: Path) -> str:
    return "data:video/mp4;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


# ---------------------------------------------------------------- JSON extraction

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+.-]*)[ \t]*\r?\n?(.*?)```", re.DOTALL)
_JSON_FENCE_TAGS = frozenset({"", "json", "json5", "jsonc"})
_DECODER = json.JSONDecoder(strict=False)  # tolerate raw newlines inside strings


def _brace_end(text: str, i: int) -> int | None:
    """Index just past the ``{...}`` group opened at ``text[i]`` (string-aware), or None."""
    depth, in_str, escaped = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def _first_object(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """First complete JSON object in ``text`` and the first parse error seen on the way.

    Tries a real JSON scan (``raw_decode``, which respects strings and escapes) at each ``{``.
    A broken object is skipped as a whole (to its matching brace, else past the error), so an
    object nested inside it is never returned and its own error is the one reported.
    """
    first_err: str | None = None
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = _DECODER.raw_decode(text, i)
        except ValueError as err:  # JSONDecodeError (has .pos), or e.g. an over-long integer
            first_err = first_err or str(err)
            pos = int(getattr(err, "pos", 0) or 0)
            i = text.find("{", max(i + 1, pos, _brace_end(text, i) or 0))
            continue
        if isinstance(obj, dict):
            return obj, None
        i = text.find("{", i + 1)
    return None, first_err


def _split_fences(text: str) -> tuple[list[str], str]:
    """(bodies of ```json / untagged fences, the text with other fences such as ```python
    blanked out)."""
    blocks: list[str] = []
    prose: list[str] = []
    last = 0
    for m in _FENCE.finditer(text):
        if m.group(1).lower() in _JSON_FENCE_TAGS:
            blocks.append(m.group(2))
        else:
            prose += [text[last : m.start()], " "]
            last = m.end()
    prose.append(text[last:])
    return blocks, "".join(prose)


def extract_json(text: str) -> dict[str, Any]:
    """The first complete JSON object in a model reply.

    Looks in ```json (or untagged) fenced blocks first, then in the reply with other fenced
    blocks (```python ...) blanked out, then in the whole reply. Raises ``ValueError`` naming
    the first JSON syntax error when no object parses.
    """
    blocks, prose = _split_fences(text)
    err: str | None = None
    for candidate in dict.fromkeys([*blocks, prose, text]):
        obj, candidate_err = _first_object(candidate)
        if obj is not None:
            return obj
        err = err or candidate_err
    raise ValueError("no JSON object found in the reply" + (f" ({err})" if err else ""))


# ---------------------------------------------------------------- transport


@dataclass
class Reply:
    text: str
    status: str  # "completed" | "incomplete"
    detail: str | None
    usage: dict[str, int]


BUDGET_CAP = 32_000  # ceiling for max_output_tokens when a reply is cut off by the budget


def _grown(reply: Reply, budget: int) -> int | None:
    """A doubled budget if ``reply`` was cut off by max_output_tokens (reasoning counts too)."""
    cut = reply.status == "incomplete" and (reply.detail or "") == "max_output_tokens"
    return min(BUDGET_CAP, budget * 2) if cut and budget < BUDGET_CAP else None


def _check(effort: str, max_output_tokens: int) -> None:
    if effort not in EFFORTS:
        raise ValueError(f"reasoning effort must be one of {EFFORTS}, got {effort!r}")
    if max_output_tokens < 16:
        raise ValueError("max_output_tokens must be >= 16 (it also counts reasoning tokens)")


def _request(
    instructions: str | None,
    content: Content,
    effort: str,
    max_output_tokens: int,
    text_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "model": settings.WONDER_MODEL,
        "input": [{"type": "message", "role": "user", "content": content}],
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
    }
    if instructions:
        req["instructions"] = instructions
    if text_format is not None:
        req["text"] = text_format
    return req


def _get(obj: Any, *path: str) -> Any:
    for name in path:
        obj = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if obj is None:
            return None
    return obj


def _respond(request: dict[str, Any], *, tag: str) -> Reply:
    """One streamed Responses call, replayed (bounded) after a transient mid-stream failure."""
    return retry.call(lambda: _respond_once(request, tag=tag), what=f"[{tag}] Wonder call")


def _events(stream: Iterable[Any]) -> Iterator[Any]:
    """The stream's events; an SDK error raised mid-stream becomes a ``WonderError`` with a code
    (``connection_error`` for a dropped or timed-out connection)."""
    try:
        yield from stream
    except openai.APIStatusError:
        raise
    except openai.APIError as err:
        raise retry.from_sdk_error(WonderError, "Wonder stream", err) from err


def _respond_once(request: dict[str, Any], *, tag: str) -> Reply:
    """One streamed call; logs its usage (also for a failed attempt: whatever the failure
    reported, 0 tokens when nothing) and returns the reply text."""
    stream = client().responses.create(stream=True, **request)
    parts: list[str] = []
    final: Any = None
    status = ""
    try:
        for ev in _events(stream):
            kind = getattr(ev, "type", "")
            if kind == "response.output_text.delta":
                parts.append(getattr(ev, "delta", "") or "")
            elif kind in ("response.completed", "response.incomplete"):
                final, status = getattr(ev, "response", None), kind.removeprefix("response.")
            elif kind == "response.failed":
                raise retry.from_event(WonderError, "Wonder response failed", ev)
            elif kind == "error":
                raise retry.from_event(WonderError, "Wonder stream error", ev)
        if not status:
            raise WonderError("Wonder stream ended without response.completed", "stream_truncated")
    except WonderError as err:
        _log(request, tag, usage_counts(err.usage), error_code=err.code or "error")
        raise
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    counts = usage_counts(_get(final, "usage"))
    _log(request, tag, counts)
    text = "".join(parts) or (_get(final, "output_text") or "")
    detail = _get(final, "incomplete_details", "reason") if status == "incomplete" else None
    return Reply(text=str(text), status=status, detail=detail, usage=counts)


def _log(request: dict[str, Any], tag: str, counts: dict[str, int], **extra: Any) -> None:
    log_usage(kind="wonder", model=str(request.get("model", "")), tag=tag, **counts, **extra)


def _sum_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in COUNT_FIELDS}


# ---------------------------------------------------------------- JSON calls


def _schema_name(model_cls: type[BaseModel]) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", model_cls.__name__)[:64] or "Output"


def _json_hint(schema: dict[str, Any]) -> str:
    return (
        "\n\nReply with only one JSON object (no prose, no code fences) that matches this "
        "JSON Schema:\n" + json.dumps(schema, separators=(",", ":"))
    )


def _is_schema_rejection(err: openai.BadRequestError) -> bool:
    param = str(getattr(err, "param", "") or "").lower()
    text = f"{err} {getattr(err, 'body', '')}".lower()
    return param.startswith("text") or "schema" in text or "format" in text


def schema_supported(model_cls: type[BaseModel]) -> bool:
    """False once the server rejected json_schema output for this model in this process."""
    return _schema_name(model_cls) not in _NO_SCHEMA


def _ask_json(
    model_cls: type[BaseModel],
    instructions: str,
    content: Content,
    effort: str,
    max_output_tokens: int,
    use_schema: bool,
    tag: str,
) -> Reply:
    name, schema = _schema_name(model_cls), model_cls.model_json_schema()
    prompt_only = _request(instructions + _json_hint(schema), content, effort, max_output_tokens)
    if not use_schema or name in _NO_SCHEMA:
        return _respond(prompt_only, tag=tag)
    fmt = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": False}}
    try:
        return _respond(_request(instructions, content, effort, max_output_tokens, fmt), tag=tag)
    except openai.BadRequestError as err:
        if not _is_schema_rejection(err):
            raise
        log.warning("[%s] json_schema format rejected (%s); using prompt-only JSON", tag, err)
        reply = _respond(prompt_only, tag=tag)  # if this also 400s, the schema was not the cause
        _NO_SCHEMA.add(name)
        return reply


def _retry_text(problem: str, bad_output: str) -> str:
    return (
        f"Your previous reply could not be used: {problem[:2000]}\n\n"
        f"Previous reply (truncated):\n{bad_output[:3000]}\n\n"
        "Reply again with ONLY the corrected JSON object."
    )


def call_json(
    model_cls: type[M],
    instructions: str,
    content: Content,
    effort: str = "high",
    max_output_tokens: int = 8000,
    *,
    tag: str = "",
    use_schema: bool = True,
    refresh: bool = False,
) -> M:
    """Ask Wonder for a JSON object and validate it as ``model_cls``.

    ``content`` is a list of Responses content parts (see ``text_part``/``image_part``/...).
    Invalid JSON or a validation error triggers ONE retry with the error appended; a second
    failure raises ``WonderJSONError``. Results are cached by request; ``refresh=True`` skips
    the cache read (the fresh result is cached again).
    """
    _check(effort, max_output_tokens)
    key = cache.key_for(
        {
            "fn": "call_json",
            "model": settings.WONDER_MODEL,
            "instructions": instructions,
            "content": content,
            "effort": effort,
            "max_output_tokens": max_output_tokens,
            "schema_name": _schema_name(model_cls),
            "schema": model_cls.model_json_schema(),
            "use_schema": use_schema,
        }
    )
    if not refresh:
        entry = cache.get_json("wonder", key)
        if isinstance(entry, dict) and "parsed" in entry:
            try:
                result = model_cls.model_validate(entry["parsed"])
            except ValueError:
                log.warning("[%s] cached Wonder entry no longer validates; calling again", tag)
            else:
                _log_cached(tag, entry)
                return result

    outputs: list[str] = []
    usage: dict[str, int] = {}
    attempt_content = list(content)
    problem = ""
    budget = max_output_tokens
    for attempt in (1, 2):
        reply = _ask_json(model_cls, instructions, attempt_content, effort, budget, use_schema, tag)
        while (bigger := _grown(reply, budget)) is not None:
            log.warning(
                "[%s] Wonder reply cut off at %d tokens; asking with %d", tag, budget, bigger
            )
            usage = _sum_usage(usage, reply.usage)
            budget = bigger
            reply = _ask_json(
                model_cls, instructions, attempt_content, effort, budget, use_schema, tag
            )
        outputs.append(reply.text)
        usage = _sum_usage(usage, reply.usage)
        try:
            result = model_cls.model_validate(extract_json(reply.text))
        except ValueError as err:  # JSONDecodeError and pydantic ValidationError are ValueErrors
            problem = str(err)
            if reply.status != "completed":
                problem = f"the reply was cut off ({reply.detail or reply.status}). " + problem
            log.warning("[%s] Wonder JSON attempt %d invalid: %s", tag, attempt, problem[:300])
            attempt_content = [*content, text_part(_retry_text(problem, reply.text))]
            continue
        cache.put_json(
            "wonder",
            key,
            {
                "schema_name": _schema_name(model_cls),
                "parsed": result.model_dump(mode="json"),
                "text": reply.text,
                "attempts": attempt,
                "usage": usage,
            },
        )
        return result
    raise WonderJSONError(
        f"{model_cls.__name__}: no valid JSON after 2 attempts: {problem[:1000]}", outputs
    )


def _log_cached(tag: str, entry: dict[str, Any]) -> None:
    counts = {k: int(entry.get("usage", {}).get(k, 0)) for k in COUNT_FIELDS}
    log_usage(kind="wonder", model=settings.WONDER_MODEL, tag=tag, cached_locally=True, **counts)


def call_text(
    prompt: str | Content,
    *,
    instructions: str | None = None,
    effort: str = "low",
    max_output_tokens: int = 200,
    tag: str = "",
    refresh: bool = False,
) -> str:
    """Plain-text Wonder call (a string prompt or a list of content parts), cached."""
    _check(effort, max_output_tokens)
    content = [text_part(prompt)] if isinstance(prompt, str) else list(prompt)
    key = cache.key_for(
        {
            "fn": "call_text",
            "model": settings.WONDER_MODEL,
            "instructions": instructions,
            "content": content,
            "effort": effort,
            "max_output_tokens": max_output_tokens,
        }
    )
    if not refresh:
        entry = cache.get_json("wonder", key)
        if isinstance(entry, dict) and isinstance(entry.get("text"), str):
            _log_cached(tag, entry)
            return entry["text"]
    budget = max_output_tokens
    reply = _respond(_request(instructions, content, effort, budget), tag=tag)
    while not reply.text.strip() and (bigger := _grown(reply, budget)) is not None:
        log.warning("[%s] Wonder reply cut off at %d tokens; asking with %d", tag, budget, bigger)
        budget = bigger
        reply = _respond(_request(instructions, content, effort, budget), tag=tag)
    if not reply.text.strip():
        raise WonderError(
            f"empty Wonder reply ({reply.status}: {reply.detail}); "
            "max_output_tokens also counts reasoning, so raise it"
        )
    if reply.status == "completed":
        cache.put_json("wonder", key, {"text": reply.text, "usage": reply.usage})
    else:
        log.warning("[%s] Wonder reply incomplete (%s); not cached", tag, reply.detail)
    return reply.text


# ---------------------------------------------------------------- video with keyframe fallback


def send_media(
    call: Callable[[Content], T],
    parts: Sequence[dict[str, Any]],
    *,
    video: dict[str, Any] | None,
    keyframes: Sequence[Path | bytes] = (),
    detail: str = "auto",
    tag: str = "",
) -> tuple[T, str]:
    """Run ``call(content)`` with the video part; if the API rejects it (HTTP 400), retry with
    the keyframes as ``input_image`` parts. Returns ``(result, "video" | "keyframes")``."""
    if video is not None:
        try:
            result = call([*parts, video])
        except openai.BadRequestError as err:
            if not keyframes:
                raise
            log.warning(
                "[%s] video part rejected (%s); using %d keyframes", tag, err, len(keyframes)
            )
        else:
            log.info("[%s] Wonder input path: video", tag)
            return result, "video"
    if not keyframes:
        raise ValueError("send_media needs a video part or keyframes")
    result = call([*parts, *(image_part(k, detail) for k in keyframes)])
    log.info("[%s] Wonder input path: keyframes (%d)", tag, len(keyframes))
    return result, "keyframes"
