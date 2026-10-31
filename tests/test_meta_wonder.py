"""Muse Wonder wrapper: JSON extraction, retry, cache, usage and fallbacks (offline fakes)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import cv2
import numpy as np
import openai
import pytest
from pydantic import BaseModel

from showtell.config import settings
from showtell.meta import retry, wonder
from showtell.meta.wonder import WonderError, WonderJSONError, call_json, extract_json, text_part


class Answer(BaseModel):
    ok: bool
    color: Literal["red", "blue"] = "red"


class Rule(BaseModel):
    select: dict[str, str]
    place_in: str | None = None


class Spec(BaseModel):
    rules: list[Rule]
    checks: list[dict[str, Any]]
    rationale: str


# An unescaped quote breaks the outer object; the nested rule object is valid on its own.
BROKEN_SPEC = (
    '{"rules": [{"select": {"size": "large"}, "place_in": "left_bin"}], "checks": [], '
    '"rationale": "big ones go "left""}'
)


def bad_request(message: str, param: str | None = None) -> openai.BadRequestError:
    body = {"message": message, "type": "invalid_request_error", "param": param, "code": None}
    response = SimpleNamespace(request=None, status_code=400, headers={})
    return openai.BadRequestError(f"Error code: 400 - {message}", response=response, body=body)


def reply(text: str, *, status: str = "completed", **usage: Any) -> list[Any]:
    u = {"input_tokens": 100, "output_tokens": 50, **usage}
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=u["input_tokens"],
            input_tokens_details=SimpleNamespace(cached_tokens=u.get("cached_tokens", 0)),
            output_tokens=u["output_tokens"],
            output_tokens_details=SimpleNamespace(reasoning_tokens=u.get("reasoning_tokens", 0)),
        ),
        output_text=text,
        incomplete_details=SimpleNamespace(reason="max_output_tokens")
        if status != "completed"
        else None,
    )
    half = len(text) // 2
    return [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta=text[:half]),
        SimpleNamespace(type="response.output_text.delta", delta=text[half:]),
        SimpleNamespace(type=f"response.{status}", response=response),
    ]


class FakeResponses:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw: Any) -> list[Any]:
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, *script: Any) -> None:
        self.responses = FakeResponses(list(script))


def broken_stream(events: list[Any], exc: BaseException) -> Any:
    """A stream that yields ``events``, then raises ``exc`` (a failure after it opened)."""
    yield from events
    raise exc


def shutdown_event() -> list[Any]:  # the documented mid-stream shutdown
    return [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(
            type="error", code="server_shutting_down", message="Server is shutting down"
        ),
    ]


def failed_event(code: str | None) -> list[Any]:
    error = SimpleNamespace(code=code, message="server_error")
    return [
        SimpleNamespace(type="response.failed", response=SimpleNamespace(error=error, usage=None))
    ]


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setattr(wonder, "_NO_SCHEMA", set())
    monkeypatch.setattr(retry, "backoff_s", lambda attempt, code: 0.0)
    return tmp_path


def use(monkeypatch: pytest.MonkeyPatch, *script: Any) -> FakeClient:
    fake = FakeClient(*script)
    monkeypatch.setattr(wonder, "client", lambda: fake)
    return fake


def usage_rows() -> list[dict[str, Any]]:
    path = settings.usage_log
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


# ------------------------------------------------------------------ extract_json


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"ok": true}', {"ok": True}),
        ('Sure! Here it is: {"ok": true, "n": 2}. Hope that helps.', {"ok": True, "n": 2}),
        ('```json\n{"ok": false}\n```', {"ok": False}),
        ('Plan:\n```\n{"a": {"b": [1, 2]}}\n```\nDone', {"a": {"b": [1, 2]}}),
        (
            '{"src": "def run(scene):\\n    d = {1: 2}  # }"}',
            {"src": "def run(scene):\n    d = {1: 2}  # }"},
        ),
        ('I think {this} is it: {"ok": true}', {"ok": True}),
        ('{"first": 1} then {"second": 2}', {"first": 1}),
        ('Use {"a": 1} as a format.\n```json\n{"ok": true}\n```', {"ok": True}),
        ('{"src": "line1\nline2"}', {"src": "line1\nline2"}),  # raw newline inside a string
        (  # a ```python block is not JSON: its {} must not win over the answer after it
            "Here is the program:\n```python\ndef run(scene):\n    done = {}\n```\n"
            '{"source": "def run(scene):\\n    pass", "notes": "sorts by size"}',
            {"source": "def run(scene):\n    pass", "notes": "sorts by size"},
        ),
        ('```python\n{"ok": true}\n```', {"ok": True}),  # mislabeled fence: last resort
        ('Note: {draft. Final: {"ok": true}', {"ok": True}),  # stray unbalanced brace
    ],
)
def test_extract_json(text: str, expected: dict[str, Any]) -> None:
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["no json here", "[1, 2, 3]", '{"ok": tru', ""])
def test_extract_json_raises(text: str) -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json(text)


@pytest.mark.parametrize(
    "text", [BROKEN_SPEC, '{"a": tru, "b": {"c": 1}}', '{"a": {"b": 1}, "c": ']
)
def test_broken_object_is_reported_not_replaced_by_a_nested_one(text: str) -> None:
    with pytest.raises(ValueError, match="no JSON object") as info:
        extract_json(text)
    assert "Expecting" in str(info.value)  # the outer object's own syntax error


# ------------------------------------------------------------------ call_json


def test_call_json_builds_request_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, reply('```json\n{"ok": true, "color": "blue"}\n```'))
    out = call_json(Answer, "Be terse.", [text_part("hi")], effort="medium", tag="t")
    assert out == Answer(ok=True, color="blue")
    req = fake.responses.calls[0]
    assert req["stream"] is True
    assert req["model"] == settings.WONDER_MODEL
    assert req["instructions"] == "Be terse."
    assert req["reasoning"] == {"effort": "medium"}
    assert req["max_output_tokens"] == 8000
    assert req["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    fmt = req["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["name"] == "Answer" and fmt["strict"] is False
    assert fmt["schema"] == Answer.model_json_schema()


def test_call_json_retries_once_with_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, reply("I cannot comply"), reply('{"ok": true}'))
    assert call_json(Answer, "x", [text_part("hi")]).ok is True
    assert len(fake.responses.calls) == 2
    retry_parts = fake.responses.calls[1]["input"][0]["content"]
    assert retry_parts[0] == text_part("hi")
    assert (
        "could not be used" in retry_parts[1]["text"]
        and "I cannot comply" in retry_parts[1]["text"]
    )


def test_call_json_retries_on_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, reply('{"ok": true, "color": "green"}'), reply('{"ok": true}'))
    assert call_json(Answer, "x", [text_part("hi")]) == Answer(ok=True)
    assert "color" in fake.responses.calls[1]["input"][0]["content"][1]["text"]


def test_retry_names_the_real_syntax_error(monkeypatch: pytest.MonkeyPatch) -> None:
    good = '{"rules": [], "checks": [], "rationale": "ok"}'
    fake = use(monkeypatch, reply(BROKEN_SPEC), reply(good))
    assert call_json(Spec, "x", [text_part("hi")], use_schema=False).rationale == "ok"
    retry_text = fake.responses.calls[1]["input"][0]["content"][1]["text"]
    assert "Expecting ',' delimiter" in retry_text and "Field required" not in retry_text


def test_call_json_raises_after_second_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, reply("nope"), reply('{"ok": "maybe"}'))
    with pytest.raises(WonderJSONError) as info:
        call_json(Answer, "x", [text_part("hi")])
    assert info.value.outputs == ["nope", '{"ok": "maybe"}']
    assert len(fake.responses.calls) == 2
    assert not (settings.CACHE_DIR / "wonder").exists()


def test_cache_hit_makes_no_client_call(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, reply('{"ok": true}'))
    first = call_json(Answer, "x", [text_part("hi")], tag="spec")
    monkeypatch.setattr(wonder, "client", lambda: pytest.fail("client must not be called"))
    assert call_json(Answer, "x", [text_part("hi")], tag="spec") == first
    rows = usage_rows()
    assert [r["cached_locally"] for r in rows] == [False, True]
    assert rows[1]["cost_usd"] == 0 and rows[1]["input_tokens"] == 100
    # A different request is a miss; refresh=True skips the cache read.
    fake = use(monkeypatch, reply('{"ok": false}'), reply('{"ok": false}'))
    assert call_json(Answer, "x", [text_part("other")]).ok is False
    assert call_json(Answer, "x", [text_part("hi")], refresh=True).ok is False
    assert len(fake.responses.calls) == 2


def test_usage_logged_with_correct_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    use(
        monkeypatch,
        reply(
            '{"ok": true}',
            input_tokens=1000,
            cached_tokens=200,
            output_tokens=500,
            reasoning_tokens=300,
        ),
    )
    call_json(Answer, "x", [text_part("hi")], tag="spec.task03")
    (row,) = usage_rows()
    assert row["kind"] == "wonder" and row["tag"] == "spec.task03"
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (1000, 200, 500)
    assert row["reasoning_tokens"] == 300
    assert row["cost_usd"] == pytest.approx((800 * 4.0 + 200 * 0.2 + 500 * 20.0) / 1e6)


def test_schema_rejection_falls_back_to_prompt_json(monkeypatch: pytest.MonkeyPatch) -> None:
    err = bad_request("text.format json_schema is not supported", param="text.format")
    fake = use(monkeypatch, err, reply('{"ok": true}'), reply('{"ok": false}'))
    assert call_json(Answer, "Be terse.", [text_part("hi")]).ok is True
    first, second = fake.responses.calls
    assert "text" in first and "text" not in second
    assert (
        second["instructions"].startswith("Be terse.") and "JSON Schema" in second["instructions"]
    )
    assert not wonder.schema_supported(Answer)
    # Remembered for the process: the next call goes straight to prompt-only JSON.
    assert call_json(Answer, "Be terse.", [text_part("hi")], refresh=True).ok is False
    assert len(fake.responses.calls) == 3 and "text" not in fake.responses.calls[2]


def test_other_bad_requests_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, bad_request("could not decode the video", param="input"))
    with pytest.raises(openai.BadRequestError):
        call_json(Answer, "x", [text_part("hi")])
    assert len(fake.responses.calls) == 1 and wonder.schema_supported(Answer)


def test_failed_stream_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(error=SimpleNamespace(message="server_error"), usage=None),
    )
    fake = use(monkeypatch, [failed])
    with pytest.raises(WonderError, match="server_error") as info:
        call_json(Answer, "x", [text_part("hi")])
    assert info.value.code is None and len(fake.responses.calls) == 1  # no code: not replayed


def test_transient_stream_error_is_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, shutdown_event(), reply('{"ok": true}'))
    assert call_json(Answer, "x", [text_part("hi")], tag="spec").ok is True
    assert len(fake.responses.calls) == 2
    first, second = usage_rows()  # one usage row per attempt
    assert first["error_code"] == "server_shutting_down" and first["cost_usd"] == 0
    assert "error_code" not in second and second["input_tokens"] == 100
    monkeypatch.setattr(wonder, "client", lambda: pytest.fail("the success must be cached"))
    assert call_json(Answer, "x", [text_part("hi")], tag="spec").ok is True


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (openai.APIConnectionError(request=None), "connection_error"),  # type: ignore[arg-type]
        (openai.APITimeoutError(request=None), "connection_error"),  # type: ignore[arg-type]
        (  # the SDK raises this for an event that carries an "error" object
            openai.APIError(
                "Server is shutting down",
                request=None,  # type: ignore[arg-type]
                body={"code": "server_shutting_down", "message": "Server is shutting down"},
            ),
            "server_shutting_down",
        ),
    ],
)
def test_sdk_errors_mid_stream_are_replayed(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException, code: str
) -> None:
    delta = SimpleNamespace(type="response.output_text.delta", delta="Rea")
    fake = use(monkeypatch, broken_stream([delta], exc), reply("Ready."))
    assert wonder.call_text("Reply with the word ready.") == "Ready."
    assert len(fake.responses.calls) == 2
    assert usage_rows()[0]["error_code"] == code


def test_stream_without_terminal_event_is_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    delta = SimpleNamespace(type="response.output_text.delta", delta="Rea")
    fake = use(monkeypatch, [delta], reply("Ready."))
    assert wonder.call_text("Reply with the word ready.") == "Ready."
    assert len(fake.responses.calls) == 2 and usage_rows()[0]["error_code"] == "stream_truncated"


def test_replays_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, *(failed_event("server_error") for _ in range(retry.MAX_ATTEMPTS)))
    with pytest.raises(WonderError, match="server_error") as info:
        call_json(Answer, "x", [text_part("hi")])
    assert info.value.code == "server_error" and info.value.retryable
    assert len(fake.responses.calls) == retry.MAX_ATTEMPTS
    assert [r["error_code"] for r in usage_rows()] == ["server_error"] * retry.MAX_ATTEMPTS
    assert not (settings.CACHE_DIR / "wonder").exists()


def test_effort_is_validated() -> None:
    with pytest.raises(ValueError, match="effort"):
        call_json(Answer, "x", [text_part("hi")], effort="none")


# ------------------------------------------------------------------ call_text / media


def test_call_text_caches_and_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, reply("Ready."))
    assert wonder.call_text("Reply with the word ready.") == "Ready."
    assert wonder.call_text("Reply with the word ready.") == "Ready."
    assert len(fake.responses.calls) == 1
    req = fake.responses.calls[0]
    assert req["reasoning"] == {"effort": "low"} and req["max_output_tokens"] == 200
    assert "instructions" not in req and "text" not in req
    fake = use(monkeypatch, reply("", status="incomplete"), reply("Done."))
    assert wonder.call_text("Say something long.") == "Done."  # cut off -> doubled budget
    assert [c["max_output_tokens"] for c in fake.responses.calls] == [200, 400]
    use(monkeypatch, reply("", status="incomplete"))
    with pytest.raises(WonderError, match="max_output_tokens"):
        wonder.call_text("Say something else.", max_output_tokens=wonder.BUDGET_CAP)


def test_call_json_grows_the_budget_when_reasoning_eats_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, reply("", status="incomplete"), reply('{"ok": true}'))
    result = call_json(Answer, "Answer.", [text_part("q")], max_output_tokens=1000)
    assert result.ok is True
    assert [c["max_output_tokens"] for c in fake.responses.calls] == [1000, 2000]


def test_send_media_falls_back_to_keyframes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frame = tmp_path / "kf.png"
    cv2.imwrite(str(frame), np.full((8, 8, 3), 255, np.uint8))
    fake = use(
        monkeypatch, bad_request("input_video is not supported for this model"), reply("A cube.")
    )
    text, path = wonder.send_media(
        lambda content: wonder.call_text(content),
        [text_part("What moves?")],
        video=wonder.video_file_part("file-1"),
        keyframes=[frame, frame],
    )
    assert (text, path) == ("A cube.", "keyframes")
    first, second = (c["input"][0]["content"] for c in fake.responses.calls)
    assert first[1] == {"type": "input_video", "file_id": "file-1"}
    assert [p["type"] for p in second] == ["input_text", "input_image", "input_image"]
    assert second[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_send_media_uses_video_when_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, reply("A cube slides left."))
    text, path = wonder.send_media(
        lambda content: wonder.call_text(content),
        [text_part("What moves?")],
        video=wonder.video_file_part("file-1"),
        keyframes=[],
    )
    assert path == "video" and text == "A cube slides left."


def test_image_part_reencodes_png_and_keeps_jpeg() -> None:
    img = np.zeros((4, 6, 3), np.uint8)
    png = cv2.imencode(".png", img)[1].tobytes()
    jpg = cv2.imencode(".jpg", img)[1].tobytes()
    part = wonder.image_part(png, detail="low")
    assert part["type"] == "input_image" and part["detail"] == "low"
    assert part["image_url"].startswith("data:image/jpeg;base64,/9j/")  # JPEG magic
    assert wonder.image_part(jpg)["image_url"].endswith(base64.b64encode(jpg).decode("ascii"))
    with pytest.raises(ValueError):
        wonder.image_part(b"not an image")
