"""SAM streaming, caching, usage, replays and concurrency with a fake AsyncOpenAI client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from showtell.config import settings
from showtell.meta import retry, sam
from showtell.perception import video_prep

LINE = (
    "<0f>0<|box;x1=10;y1=20;x2=30;y2=40;w=320;h=240;c=0.9|>"
    "<|mask;x=0;y=0;c=0.9;data=21,21,!!!!J!0c|>\n"
)


def ev(kind: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **kw)


def done(frames: int = 50) -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, video_frames_processed=frames)
    return ev("response.completed", response=SimpleNamespace(usage=usage))


def shutdown() -> SimpleNamespace:  # the documented mid-stream shutdown event
    return ev("error", code="server_shutting_down", message="Server is shutting down", param=None)


def failed(code: str | None, frames: int | None = None) -> SimpleNamespace:
    usage = None if frames is None else SimpleNamespace(video_frames_processed=frames)
    error = SimpleNamespace(code=code, message="boom")
    return ev("response.failed", response=SimpleNamespace(error=error, usage=usage))


class FakeStream:
    """Like the SDK's AsyncStream; an exception in ``events`` is raised mid-stream."""

    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.closed = False

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    def __aiter__(self) -> Any:
        return self._gen()

    async def _gen(self) -> Any:
        for e in self.events:
            await asyncio.sleep(0)
            if isinstance(e, BaseException):
                raise e
            yield e


class FakeResponses:
    """``script[concept]``: the events of every request for it; ``attempts[concept]``: one event
    list per request, consumed in order (then ``script``)."""

    def __init__(
        self, script: dict[str, list[Any]], attempts: dict[str, list[list[Any]]] | None = None
    ) -> None:
        self.script = script
        self.attempts = {k: list(v) for k, v in (attempts or {}).items()}
        self.calls: list[dict[str, Any]] = []
        self.streams: list[FakeStream] = []

    def stream(self, **kw: Any) -> Any:
        pytest.fail("the SDK responses.stream helper must not be used (it can double-send)")

    async def create(self, *, stream: bool = False, **kw: Any) -> FakeStream:
        assert stream is True
        self.calls.append(kw)
        concept = kw["input"][0]["content"][0]["text"]
        queue = self.attempts.get(concept)
        self.streams.append(FakeStream(queue.pop(0) if queue else self.script[concept]))
        return self.streams[-1]


class FakeAsyncClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setattr(retry, "backoff_s", lambda attempt, code: 0.0)
    return tmp_path


def usage_rows() -> list[dict[str, Any]]:
    path = settings.usage_log
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def run_concept(responses: FakeResponses, concept: str, **kw: Any) -> tuple[Any, str, int]:
    async def go() -> tuple[Any, str, int]:
        return await sam.track_concept(
            "file-1",
            concept,
            sem=asyncio.Semaphore(2),
            client=FakeAsyncClient(responses),
            media_key="sha-abc",
            **kw,
        )

    return asyncio.run(go())


def test_track_concept_streams_caches_and_logs_cost() -> None:
    responses = FakeResponses(
        {
            "red block": [
                ev("response.created"),
                ev("response.output_text.delta", delta=LINE),
                done(50),
            ]
        }
    )
    tracks, raw, frames = run_concept(responses, "  red   block ")
    assert raw == LINE and frames == 50
    assert tracks[0][0].x1 == 10 and tracks[0][0].conf == pytest.approx(0.9)
    req = responses.calls[0]
    assert set(req) == {"model", "input", "metadata"}
    assert req["model"] == settings.SAM_MODEL
    assert req["metadata"] == {"include_confidence": "true", "mask_encoding": "one_bit"}
    assert req["input"][0]["content"] == [
        {"type": "input_text", "text": "red block"},
        {"type": "input_video", "file_id": "file-1"},
    ]
    assert responses.streams[0].closed
    # Second call is a local cache hit: no request, logged at $0.
    tracks2, raw2, frames2 = run_concept(responses, "red block")
    assert (tracks2, raw2, frames2) == (tracks, raw, frames)
    assert len(responses.calls) == 1
    rows = usage_rows()
    assert [r["cached_locally"] for r in rows] == [False, True]
    assert rows[0]["cost_usd"] == pytest.approx(50 * 0.20 / 1000)
    assert rows[1]["cost_usd"] == 0 and rows[1]["video_frames"] == 50


def test_every_request_is_sent_once_and_logged() -> None:
    """Raw events from the first request: no second (billed, unlogged) request per concept."""
    responses = FakeResponses({"hand": [ev("response.output_text.delta", delta=LINE), done(73)]})
    tracks, raw, frames = run_concept(responses, "hand")
    assert list(tracks) == [0] and raw == LINE and frames == 73
    assert len(responses.calls) == len(usage_rows()) == 1


def test_short_clip_is_billed_the_50_frame_minimum() -> None:
    responses = FakeResponses({"glass": [ev("response.output_text.delta", delta=LINE), done(12)]})
    run_concept(responses, "glass")
    assert usage_rows()[0]["cost_usd"] == pytest.approx(50 * 0.20 / 1000)


def test_zero_matches_is_a_valid_cached_result() -> None:
    responses = FakeResponses({"unicorn": [ev("response.created"), done(50)]})
    tracks, raw, _ = run_concept(responses, "unicorn")
    assert tracks == {} and raw == ""
    run_concept(responses, "unicorn")
    assert len(responses.calls) == 1


def test_permanent_failure_is_not_replayed_or_cached() -> None:
    responses = FakeResponses({"cup": [failed("invalid_prompt")]})
    with pytest.raises(sam.SamError, match="boom") as info:
        run_concept(responses, "cup", frames_hint=120)
    assert info.value.code == "invalid_prompt" and not info.value.retryable
    assert len(responses.calls) == 1
    (row,) = usage_rows()  # the failed attempt still has a usage row (estimated: may be billed)
    assert row["error_code"] == "invalid_prompt" and row["estimated"] is True
    assert row["video_frames"] == 120 and row["cost_usd"] == pytest.approx(120 * 0.20 / 1000)
    assert not (settings.CACHE_DIR / "sam").exists()


def test_transient_stream_error_is_replayed_and_logged_per_attempt() -> None:
    responses = FakeResponses(
        {},
        attempts={
            "hand": [
                [ev("response.created"), shutdown()],
                [failed("server_error", frames=80)],
                [ev("response.output_text.delta", delta=LINE), done(50)],
            ]
        },
    )
    tracks, raw, frames = run_concept(responses, "hand")
    assert list(tracks) == [0] and raw == LINE and frames == 50
    assert len(responses.calls) == 3
    rows = usage_rows()
    assert [r.get("error_code") for r in rows] == ["server_shutting_down", "server_error", None]
    assert [r["video_frames"] for r in rows] == [50, 80, 50]  # estimate (minimum), reported, ok
    assert [r.get("estimated") for r in rows] == [True, False, None]
    # Only the completed response was cached.
    run_concept(responses, "hand")
    assert len(responses.calls) == 3


def test_connection_drop_mid_stream_is_replayed() -> None:
    dropped = openai.APIConnectionError(request=None)  # type: ignore[arg-type]
    responses = FakeResponses(
        {"hand": [ev("response.output_text.delta", delta=LINE), done(50)]},
        attempts={"hand": [[ev("response.output_text.delta", delta=LINE), dropped]]},
    )
    _, raw, _ = run_concept(responses, "hand")
    assert raw == LINE  # the partial text of the dropped attempt is discarded
    assert len(responses.calls) == 2
    assert usage_rows()[0]["error_code"] == "connection_error"


def test_replays_are_bounded() -> None:
    responses = FakeResponses({"hand": [ev("response.output_text.delta", delta=LINE)]})
    with pytest.raises(sam.SamError, match="without response.completed") as info:
        run_concept(responses, "hand")
    assert info.value.code == "stream_truncated"
    assert len(responses.calls) == len(usage_rows()) == retry.MAX_ATTEMPTS
    assert not (settings.CACHE_DIR / "sam").exists()


def test_track_runs_concepts_concurrently_and_uploads_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    uploads: list[Path] = []
    monkeypatch.setattr(sam.files, "upload", lambda p, force=False: uploads.append(p) or "file-9")
    script = {
        "red block": [ev("response.output_text.delta", delta=LINE), done(50)],
        "glass": [done(50)],
    }
    responses = FakeResponses(script)
    monkeypatch.setattr(sam, "aclient", lambda: FakeAsyncClient(responses))
    out = sam.track(video, ["red block", "glass", "red block"], max_concurrency=2)
    assert list(out) == ["red block", "glass"]
    assert list(out["red block"]) == [0] and out["glass"] == {}
    assert len(uploads) == 1 and len(responses.calls) == 2
    # Fully cached rerun: no upload and no client needed.
    monkeypatch.setattr(sam, "aclient", lambda: pytest.fail("client should not be created"))
    assert sam.track(video, ["glass", "red block"]) == {"glass": {}, "red block": out["red block"]}
    assert len(uploads) == 1


def test_results_are_keyed_by_the_callers_strings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(sam.files, "upload", lambda p, force=False: "file-1")
    hit = [ev("response.output_text.delta", delta=LINE), done(50)]
    responses = FakeResponses({"red block": hit, "Red block": [done(50)]})
    monkeypatch.setattr(sam, "aclient", lambda: FakeAsyncClient(responses))
    asked = ["red block ", "Red  block", "red block"]
    out = sam.track(video, asked)
    assert list(out) == asked
    assert out["red block "] == out["red block"] and list(out["red block"]) == [0]
    assert out["Red  block"] == {}
    sent = sorted(c["input"][0]["content"][0]["text"] for c in responses.calls)
    assert sent == ["Red block", "red block"]  # one request per distinct cleaned concept
    # The same keys come back from a fully cached rerun.
    assert list(sam.track(video, asked)) == asked and len(responses.calls) == 2


def test_blank_concept_raises(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    with pytest.raises(ValueError, match="blank SAM concept"):
        sam.track(video, ["hand", "  "])


def test_track_rejects_non_mp4(tmp_path: Path) -> None:
    bad = tmp_path / "clip.webm"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError, match="MP4/MOV"):
        sam.track(bad, ["hand"])


def test_mov_is_remuxed_to_mp4_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mov = video_prep.make_test_clip(tmp_path / "clip.MOV", seconds=1)
    uploads: list[Path] = []
    monkeypatch.setattr(sam.files, "upload", lambda p, force=False: uploads.append(p) or "file-1")
    responses = FakeResponses(
        {"red square": [ev("response.output_text.delta", delta=LINE), done()]}
    )
    monkeypatch.setattr(sam, "aclient", lambda: FakeAsyncClient(responses))
    assert list(sam.track(mov, ["red square"])["red square"]) == [0]
    (sent,) = uploads
    assert sent.suffix == ".mp4" and sent.parent == settings.CACHE_DIR / "video"
    assert video_prep.probe(sent)[:4] == video_prep.probe(mov)[:4]  # same frames, no re-encode


def test_stale_file_id_is_reuploaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    ids = iter(["file-old", "file-new"])
    invalidated: list[str] = []
    monkeypatch.setattr(sam.files, "upload", lambda p, force=False: next(ids))
    monkeypatch.setattr(sam.files, "invalidate", invalidated.append)
    responses = FakeResponses({"hand": [ev("response.output_text.delta", delta=LINE), done(50)]})
    original = responses.create

    async def create(**kw: Any) -> FakeStream:
        if kw["input"][0]["content"][1]["file_id"] == "file-old":
            body = {"message": "File not found", "code": "file_not_found"}
            resp = SimpleNamespace(request=None, status_code=404, headers={})
            raise openai.NotFoundError("Error code: 404", response=resp, body=body)
        return await original(**kw)

    responses.create = create  # type: ignore[method-assign]
    monkeypatch.setattr(sam, "aclient", lambda: FakeAsyncClient(responses))
    assert list(sam.track(video, ["hand"])["hand"]) == [0]
    assert invalidated == ["file-old"]
    assert usage_rows()[0].get("error_code") is None  # the rejected request is not logged
