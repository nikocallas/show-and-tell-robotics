"""SAM 3.1 video tracking: one concept per streamed request, parsed into per-object box tracks.

Wire format (https://dev.meta.ai/docs/sam/reading-segmentation): one line per frame, a ``<Nf>``
marker then comma-separated records
``ID<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|><|mask;x=0;y=0[;c=..];data=H,W,<enc>payload|>``.
Frames may skip or arrive out of order and object ids are sparse, so everything is keyed by the
frame marker and the id. Only boxes are used for logic; masks stay in the raw text for overlays.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

import meta_sam_parser as msp
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from showtell.config import settings
from showtell.meta import cache, files, retry
from showtell.meta.client import aclient
from showtell.meta.usage import log_usage, sam_cost, usage_counts

log = logging.getLogger(__name__)
T = TypeVar("T")

METADATA = {"include_confidence": "true", "mask_encoding": "one_bit"}  # values must be strings
MAX_FRAMES = 15_000
VIDEO_SUFFIXES = (".mp4", ".mov")  # SAM's containers; a MOV is remuxed to MP4 for the upload
# Diagnostics after which the parser still kept the box (only the mask was dropped).
_MASK_ONLY_CODES = frozenset({"invalid_mask_size", "invalid_mask_payload"})


class FrameBox(BaseModel):
    """One object's box on one frame, in source pixels with inclusive ``x2``/``y2`` (wire form)."""

    model_config = ConfigDict(frozen=True)

    frame: int
    x1: float
    y1: float
    x2: float
    y2: float
    w: int  # source frame width
    h: int  # source frame height
    conf: float | None = None

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return (self.x2 - self.x1 + 1) * (self.y2 - self.y1 + 1)


Tracks = dict[int, list[FrameBox]]  # object id -> boxes sorted by frame


class ConceptResult(NamedTuple):
    tracks: Tracks
    raw_text: str
    frames_billed: int


class SamError(retry.CallError):
    """The SAM stream failed, ended incomplete, or ended without ``response.completed``;
    ``code`` is the API's error code when known."""


# ---------------------------------------------------------------- parsing

_MARK_MID_LINE = re.compile(r"(?<=[^\n])(?=<\d+f(?:;[^>]*)?>)")
_LINE_HEAD = re.compile(r"\s*<(\d+)f(?:;[^>]*)?>")
_RECORD = re.compile(r"(\d+)((?:<\|[^|]*\|>)+)")
_TOKEN = re.compile(r"<\|([A-Za-z][\w.-]*)(?:;([^|]*))?\|>")
_BOX_TOKEN = re.compile(r"<\|box;([^|]*)\|>")
_MASK_DATA = re.compile(r"(data=\d+,\d+,[!~])([^|]*)")


def normalize_text(text: str) -> str:
    """Put every ``<Nf>`` marker at the start of a line (in case deltas lacked newlines).

    Mask payloads use a base85 alphabet without ``<``, so a marker never occurs inside one.
    """
    return _MARK_MID_LINE.sub("\n", text)


def _fields(body: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (body or "").split(";"):
        key, sep, value = part.partition("=")
        key = key.strip()
        if key and sep and key not in out:
            out[key] = value.strip()
    return out


def _box(frame: int, f: dict[str, str]) -> FrameBox | None:
    try:
        x1, y1, x2, y2, w, h = (int(f[k]) for k in ("x1", "y1", "x2", "y2", "w", "h"))
    except (KeyError, ValueError):
        return None
    if w <= 0 or h <= 0 or x1 < 0 or y1 < 0 or x2 < x1 or y2 < y1 or x2 >= w or y2 >= h:
        return None
    conf: float | None = None
    try:
        c = float(f["c"]) if "c" in f else None
    except ValueError:
        c = None
    if c is not None and 0.0 <= c <= 1.0:
        conf = c
    return FrameBox(frame=frame, x1=x1, y1=y1, x2=x2, y2=y2, w=w, h=h, conf=conf)


def _to_tracks(found: dict[int, dict[int, FrameBox]]) -> Tracks:
    return {oid: [boxes[f] for f in sorted(boxes)] for oid, boxes in sorted(found.items())}


def parse_sam_text(text: str) -> Tracks:
    """Regex parser: frame marker per line, then each ``ID<|...|>...`` record's box by key.

    Robust to any mask payload characters (tokens end at ``|>``; fields are read by key, so the
    optional ``c`` can sit anywhere). Malformed records are skipped.
    """
    found: dict[int, dict[int, FrameBox]] = {}
    for line in normalize_text(text).splitlines():
        head = _LINE_HEAD.match(line)
        if head is None:
            continue
        frame = int(head.group(1))
        for rec in _RECORD.finditer(line, head.end()):
            bodies = [body for name, body in _TOKEN.findall(rec.group(2)) if name == "box"]
            if len(bodies) != 1:
                continue
            box = _box(frame, _fields(bodies[0]))
            if box is not None:
                found.setdefault(int(rec.group(1)), {})[frame] = box
    return _to_tracks(found)


async def _replay(text: str) -> AsyncIterator[dict[str, Any]]:
    """Synthetic Responses events for the official parser (one delta per line)."""
    lane = {"item_id": "msg_0", "output_index": 0, "content_index": 0}
    for line in text.splitlines(keepends=True):
        yield {"type": "response.output_text.delta", "delta": line, **lane}
    yield {"type": "response.output_text.done", "text": text, **lane}
    yield {"type": "response.completed", "response": {}}


def _frame_size(text: str) -> tuple[int, int]:
    m = _BOX_TOKEN.search(text)
    f = _fields(m.group(1)) if m else {}
    try:
        return int(f.get("w", 0)), int(f.get("h", 0))
    except ValueError:
        return 0, 0


async def _official(text: str) -> tuple[Tracks, list[Any]]:
    """Parse with Meta's ``meta_sam_parser`` (right/bottom converted back to inclusive) and
    return its error-severity diagnostics too. The parser never raises on a malformed line: it
    drops the rest of that line and records a diagnostic instead."""
    text = normalize_text(text)
    parsed = msp.parse_responses_stream(_replay(text), msp.video_segmentation_format())
    result = await parsed.final_result()
    w, h = _frame_size(text)
    found: dict[int, dict[int, FrameBox]] = {}
    for r in result.records:
        if r.kind != "box" or r.frame is None:
            continue
        found.setdefault(int(r.object_id), {})[r.frame.frame_index] = FrameBox(
            frame=r.frame.frame_index,
            x1=r.left,
            y1=r.top,
            x2=r.right - 1,
            y2=r.bottom - 1,
            w=w,
            h=h,
            conf=r.confidence,
        )
    return _to_tracks(found), [d for d in result.diagnostics if d.severity == "error"]


async def parse_official_async(text: str) -> Tracks:
    """Official parser only: records on a line after a rejected one are dropped, as it does."""
    return (await _official(text))[0]


def parse_sam_text_official(text: str) -> Tracks:
    return run_sync(lambda: parse_official_async(text))


def merge_tracks(primary: Tracks, extra: Tracks) -> tuple[Tracks, int]:
    """``primary`` plus each ``extra`` box for an (object, frame) it lacks; also the count added."""
    found = {oid: {b.frame: b for b in boxes} for oid, boxes in primary.items()}
    added = 0
    for oid, boxes in extra.items():
        have = found.setdefault(oid, {})
        for b in boxes:
            if b.frame not in have:
                have[b.frame], added = b, added + 1
    return _to_tracks(found), added


async def parse_async(text: str) -> Tracks:
    """Official parser; when it rejected a line, the boxes it dropped are recovered from the
    regex parse (with a warning); any parser exception falls back to the regex parse."""
    try:
        tracks, errors = await _official(text)
    except Exception as err:  # any parser failure falls back to the regex
        log.warning("official SAM parser failed (%s); using the regex fallback", err)
        return parse_sam_text(text)
    dropped = [d for d in errors if d.code not in _MASK_ONLY_CODES]
    if not dropped:
        if errors:
            log.debug("SAM parser: %d mask-only diagnostic(s); boxes kept", len(errors))
        return tracks
    merged, added = merge_tracks(tracks, parse_sam_text(text))
    where = ", ".join(f"{d.code}@line {d.line}" for d in dropped[:5])
    log.warning(
        "SAM parser rejected %d record(s) (%s%s); recovered %d box(es) with the regex parser",
        len(dropped),
        where,
        ", ..." if len(dropped) > 5 else "",
        added,
    )
    return merged


def parse(text: str) -> Tracks:
    """Official parser, plus regex recovery of rejected lines (see ``parse_async``)."""
    return run_sync(lambda: parse_async(text))


def short_line(line: str, keep: int = 12) -> str:
    """A wire line with mask payloads truncated, for logs and reports."""
    return _MASK_DATA.sub(lambda m: m.group(1) + m.group(2)[:keep] + "...", line)


# ---------------------------------------------------------------- async plumbing


async def _await(factory: Callable[[], Awaitable[T]]) -> T:
    return await factory()


def run_sync(factory: Callable[[], Awaitable[T]]) -> T:
    """Run an async factory from sync code, even when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await(factory))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_await(factory))).result()


def _get(obj: Any, *path: str) -> Any:
    for name in path:
        obj = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if obj is None:
            return None
    return obj


async def _collect(events: Any) -> tuple[str, Any]:
    """Accumulate output-text deltas until ``response.completed``; return (text, usage).

    A mid-stream failure raises ``SamError`` with the API's code (``connection_error`` for a
    dropped connection, ``stream_truncated`` when no terminal event arrived)."""
    parts: list[str] = []
    usage: Any = None
    done = False
    try:
        async for ev in events:
            kind = getattr(ev, "type", "")
            if kind == "response.output_text.delta":
                parts.append(getattr(ev, "delta", "") or "")
            elif kind == "response.completed":
                usage, done = _get(ev, "response", "usage"), True
            elif kind == "response.incomplete":
                reason = _get(ev, "response", "incomplete_details", "reason")
                raise SamError(
                    f"SAM response incomplete: {reason or 'unknown reason'}",
                    usage=_get(ev, "response", "usage"),
                )
            elif kind == "response.failed":
                raise retry.from_event(SamError, "SAM response failed", ev)
            elif kind == "error":
                raise retry.from_event(SamError, "SAM stream error", ev)
    except openai.APIStatusError:
        raise
    except openai.APIError as err:  # raised by the SDK while reading the open stream
        raise retry.from_sdk_error(SamError, "SAM stream", err) from err
    if not done:
        raise SamError("SAM stream ended without response.completed", "stream_truncated")
    return "".join(parts), usage


async def _stream(ac: AsyncOpenAI, request: dict[str, Any]) -> tuple[str, Any]:
    """One streamed request read as raw Responses events, so each attempt is sent (and billed)
    once. The SDK's ``responses.stream`` helper is not used: it can reject the event sequence
    after the request was sent, and re-sending would bill the clip twice."""
    async with await ac.responses.create(stream=True, **request) as stream:
        return await _collect(stream)


def _log_failed(err: SamError, *, concept: str, tag: str, frames_hint: int) -> None:
    """Usage row for a failed attempt: the frames it reported, else an estimate of what it may
    have billed (the probed frame count, at least the 50-frame minimum), flagged ``estimated``."""
    counts = usage_counts(err.usage)
    estimated = not counts["video_frames"]
    if estimated:
        counts["video_frames"] = max(int(frames_hint), settings.SAM_MIN_FRAMES_PER_REQUEST)
    log_usage(
        kind="sam",
        model=settings.SAM_MODEL,
        tag=tag,
        cost_usd=sam_cost(counts["video_frames"], counts["images"]),
        concept=concept,
        error_code=err.code or "error",
        estimated=estimated,
        **counts,
    )


async def _send(
    ac: AsyncOpenAI, request: dict[str, Any], *, concept: str, tag: str, frames_hint: int
) -> tuple[str, Any]:
    """``_stream``, replayed (bounded) after a transient mid-stream failure; every failed
    attempt gets its own usage row."""

    async def attempt() -> tuple[str, Any]:
        try:
            return await _stream(ac, request)
        except SamError as err:
            _log_failed(err, concept=concept, tag=tag, frames_hint=frames_hint)
            raise

    return await retry.acall(attempt, what=f"SAM {concept!r}")


def _cache_key(media: str, concept: str) -> str:
    return cache.key_for(
        {"model": settings.SAM_MODEL, "media": media, "concept": concept, "metadata": METADATA}
    )


def _clean(concept: str) -> str:
    return " ".join(concept.split())


async def track_concept(
    file_id: str,
    concept: str,
    *,
    sem: asyncio.Semaphore,
    media_key: str | None = None,
    client: AsyncOpenAI | None = None,
    frames_hint: int = 0,
    tag: str = "",
    refresh: bool = False,
) -> tuple[Tracks, str, int]:
    """Track one concept (a short noun phrase) in an uploaded video.

    Returns ``(tracks, raw_text, frames_billed)``. Cached by ``(media_key or file_id, concept,
    metadata)``; zero matches is a valid (cached) empty result. Transient mid-stream failures are
    replayed (bounded); only a completed response is cached.
    """
    concept = _clean(concept)
    if not concept:
        raise ValueError("empty SAM concept")
    key = _cache_key(media_key or file_id, concept)
    if not refresh:
        hit = cache.get_json("sam", key)
        if isinstance(hit, dict) and isinstance(hit.get("text"), str):
            frames = int(hit.get("frames_billed", 0))
            log_usage(
                kind="sam",
                model=settings.SAM_MODEL,
                tag=tag,
                cached_locally=True,
                video_frames=frames,
                concept=concept,
            )
            return await parse_async(hit["text"]), hit["text"], frames
    if not file_id:
        raise ValueError("track_concept needs a file_id when the result is not cached")
    request = {
        "model": settings.SAM_MODEL,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": concept},
                    {"type": "input_video", "file_id": file_id},
                ],
            }
        ],
        "metadata": dict(METADATA),
    }

    async def send(ac: AsyncOpenAI) -> tuple[str, Any]:
        return await _send(ac, request, concept=concept, tag=tag, frames_hint=frames_hint)

    async with sem:
        if client is None:
            async with aclient() as ac:
                text, usage = await send(ac)
        else:
            text, usage = await send(client)
    counts = usage_counts(usage)
    frames = counts["video_frames"]
    if not frames:
        log.warning("SAM usage had no video_frames_processed; billing the probed frame count")
        frames = counts["video_frames"] = int(frames_hint)
    log_usage(
        kind="sam",
        model=settings.SAM_MODEL,
        tag=tag,
        cost_usd=sam_cost(frames, counts["images"]),
        concept=concept,
        **counts,
    )
    cache.put_json(
        "sam",
        key,
        {
            "concept": concept,
            "media": media_key or file_id,
            "file_id": file_id,
            "frames_billed": frames,
            "metadata": METADATA,
            "text": text,
        },
    )
    return await parse_async(text), text, frames


# ---------------------------------------------------------------- whole-video tracking


def check_video(video_path: Path) -> tuple[Path, int]:
    """Validate container suffix; return ``(path, frame_count or 0)`` and warn past the limit."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError(f"SAM needs MP4/MOV (H.264/H.265/MPEG-4) video, got {path.name}")
    try:
        from showtell.perception.video_prep import probe

        frames = probe(path).frames
    except Exception as err:  # probing is advisory only
        log.debug("could not probe %s: %s", path, err)
        frames = 0
    if frames > MAX_FRAMES:
        log.warning(
            "%s has %d frames; SAM accepts at most %d per request", path, frames, MAX_FRAMES
        )
    return path, frames


def _upload_path(path: Path) -> Path:
    """The file to upload: an MP4 as is, a MOV remuxed losslessly to a cached MP4 (same frames
    and timestamps), since the Files API documents only ``video/mp4`` for video."""
    if path.suffix.lower() == ".mp4":
        return path
    from showtell.perception.video_prep import remux_mp4

    return remux_mp4(path)


async def track_full_async(
    video_path: Path,
    concepts: Iterable[str],
    *,
    max_concurrency: int = 4,
    tag: str = "",
    refresh: bool = False,
) -> dict[str, ConceptResult]:
    """Track several concepts concurrently (one request each, bounded by a semaphore).

    The result is keyed by the caller's concept strings exactly as given; strings that differ
    only in whitespace share one request. A blank concept raises ``ValueError``.
    """
    path, frames = check_video(video_path)
    clean = {c: _clean(c) for c in concepts}
    if blank := [c for c, name in clean.items() if not name]:
        raise ValueError(f"blank SAM concept(s): {blank!r}")
    names = list(dict.fromkeys(clean.values()))
    if not names:
        return {}
    sha = cache.file_sha256(path)
    sem = asyncio.Semaphore(max(1, max_concurrency))
    need_api = refresh or any(cache.get_json("sam", _cache_key(sha, c)) is None for c in names)
    if not need_api:  # everything cached: no key, upload or client needed
        done = [await track_concept("", c, sem=sem, media_key=sha, tag=tag) for c in names]
        by_name = {c: ConceptResult(*r) for c, r in zip(names, done, strict=True)}
        return {c: by_name[name] for c, name in clean.items()}

    upload = _upload_path(path)
    file_id = files.upload(upload)
    async with aclient() as ac:

        async def run(fid: str, todo: list[str]) -> list[Any]:
            return await asyncio.gather(
                *(
                    track_concept(
                        fid,
                        c,
                        sem=sem,
                        media_key=sha,
                        client=ac,
                        frames_hint=frames,
                        tag=tag,
                        refresh=refresh,
                    )
                    for c in todo
                ),
                return_exceptions=True,
            )

        results = dict(zip(names, await run(file_id, names), strict=True))
        stale = [c for c, r in results.items() if files.is_missing_file(r)]
        if stale:  # cached file id unknown to the server (deleted, or another team's key)
            log.warning("file %s not found on the server; re-uploading", file_id)
            files.invalidate(file_id)
            file_id = files.upload(upload, force=True)
            results.update(zip(stale, await run(file_id, stale), strict=True))
    by_name: dict[str, ConceptResult] = {}
    for c in names:
        r = results[c]
        if isinstance(r, BaseException):
            raise r
        by_name[c] = ConceptResult(*r)
    return {c: by_name[name] for c, name in clean.items()}


async def track_async(
    video_path: Path,
    concepts: Iterable[str],
    *,
    max_concurrency: int = 4,
    tag: str = "",
    refresh: bool = False,
) -> dict[str, Tracks]:
    full = await track_full_async(
        video_path, concepts, max_concurrency=max_concurrency, tag=tag, refresh=refresh
    )
    return {c: r.tracks for c, r in full.items()}


def track_full(
    video_path: Path,
    concepts: Iterable[str],
    *,
    max_concurrency: int = 4,
    tag: str = "",
    refresh: bool = False,
) -> dict[str, ConceptResult]:
    """Sync wrapper of ``track_full_async`` (safe inside a running event loop)."""
    names = list(concepts)
    return run_sync(
        lambda: track_full_async(
            video_path, names, max_concurrency=max_concurrency, tag=tag, refresh=refresh
        )
    )


def track(
    video_path: Path,
    concepts: Iterable[str],
    *,
    max_concurrency: int = 4,
    tag: str = "",
    refresh: bool = False,
) -> dict[str, Tracks]:
    """Sync: ``{concept: {object_id: [FrameBox, ...]}}`` for an MP4/MOV video, keyed by the
    caller's concept strings."""
    full = track_full(
        video_path, concepts, max_concurrency=max_concurrency, tag=tag, refresh=refresh
    )
    return {c: r.tracks for c, r in full.items()}
