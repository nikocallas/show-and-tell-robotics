"""`showtell smoke` end to end with fake API clients (offline), plus the missing-key path."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from showtell.config import settings
from showtell.meta import files, sam, smoke, wonder

SAM_LINE = (
    "<0f>0<|box;x1=40;y1=300;x2=159;y2=419;w=960;h=720;c=0.97|>"
    "<|mask;x=0;y=0;c=0.97;data=120,120,!!!!J!0c[q=Pj_zs=*4C4|>\n"
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(wonder, "_NO_SCHEMA", set())
    return tmp_path


def test_missing_key_writes_report_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MODEL_API_KEY", "")
    report = smoke.run_smoke(Path("nope.mp4"))
    assert report["ok"] is False
    assert report["message"] == "MODEL_API_KEY missing: add it to .env"
    saved = json.loads((settings.SHOWTELL_DATA_DIR / "smoke_report.json").read_text())
    assert saved["ok"] is False and saved["message"] == report["message"]


def _events(text: str) -> list[Any]:
    usage = SimpleNamespace(input_tokens=500, output_tokens=40, video_frames_processed=50)
    done = SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage))
    return [SimpleNamespace(type="response.output_text.delta", delta=text), done]


class FakeWonder:
    def __init__(self) -> None:
        self.responses = self
        self.contents: list[list[dict[str, Any]]] = []

    def create(self, **kw: Any) -> list[Any]:
        content = kw["input"][0]["content"]
        self.contents.append(content)
        if any(p["type"] == "input_video" for p in content):
            body = {"message": "input_video not supported", "param": "input", "code": None}
            resp = SimpleNamespace(request=None, status_code=400, headers={})
            raise openai.BadRequestError("Error code: 400", response=resp, body=body)
        if "text" in kw:
            return _events('{"ok": true}')
        if "ready" in content[0]["text"]:
            return _events("Ready.")
        return _events("A red square slides to the right.")


class FakeSam:
    def __init__(self) -> None:
        self.responses = self
        self.concepts: list[str] = []

    async def __aenter__(self) -> FakeSam:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def create(self, *, stream: bool = False, **kw: Any) -> Any:
        assert stream is True
        self.concepts.append(kw["input"][0]["content"][0]["text"])
        events = _events(SAM_LINE)

        class _S:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def __aiter__(self) -> Any:
                for e in events:
                    yield e

        return _S()


def test_smoke_end_to_end_with_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key-not-real")
    fw, fs = FakeWonder(), FakeSam()
    monkeypatch.setattr(wonder, "client", lambda: fw)
    monkeypatch.setattr(sam, "aclient", lambda: fs)
    uploads = SimpleNamespace(create=lambda **kw: SimpleNamespace(id="file-smoke"))
    monkeypatch.setattr(files, "client", lambda: SimpleNamespace(files=uploads))

    report = smoke.run_smoke(tmp_path / "missing.mp4")  # no human clip -> synthetic clip

    steps = report["steps"]
    assert report["ok"] is True, steps
    assert steps["wonder_text"]["reply"] == "Ready."
    assert steps["wonder_json"]["result"] == {"ok": True}
    assert steps["clip"]["concept"] == "red square" and steps["clip"]["frames"] == 50
    assert steps["wonder_video"]["path_used"] == "keyframes" == report["video_path_used"]
    assert steps["wonder_video"]["keyframes"] == 8
    assert fs.concepts == ["red square"]
    assert steps["sam"]["n_tracks"] == 1 and steps["sam"]["frames_billed"] == 50
    assert steps["sam"]["example_box"]["x1"] == 40
    assert "data=120,120,!!!!J!0c[q=Pj..." in steps["sam"]["example_raw_line"]
    assert report["cost_usd"] > 0 and report["api_calls"] == 4  # the rejected video call is free
    saved = json.loads((settings.SHOWTELL_DATA_DIR / "smoke_report.json").read_text())
    assert saved["ok"] is True
    assert "test-key-not-real" not in json.dumps(saved)
