"""Cost math, usage logging and the `showtell cost` summary."""

from __future__ import annotations

import io
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from showtell.config import settings
from showtell.meta import usage
from showtell.meta.usage import log_usage, sam_cost, summarize, usage_counts, wonder_cost


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    return tmp_path


def test_wonder_cost() -> None:
    assert wonder_cost(1_000_000) == pytest.approx(4.00)
    assert wonder_cost(1_000_000, cached_tokens=1_000_000) == pytest.approx(0.20)
    assert wonder_cost(0, output_tokens=1_000_000) == pytest.approx(20.00)
    assert wonder_cost(10_000, 2_000, 3_000) == pytest.approx(
        (8_000 * 4 + 2_000 * 0.2 + 3_000 * 20) / 1e6
    )


def test_sam_cost_bills_a_50_frame_minimum_per_request() -> None:
    assert sam_cost(0) == 0
    assert sam_cost(1) == pytest.approx(50 * 0.0002)
    assert sam_cost(50) == pytest.approx(0.01)
    assert sam_cost(120) == pytest.approx(120 * 0.0002)
    assert sam_cost(images=4) == pytest.approx(4 * 0.0025)


def test_usage_counts_from_sdk_object_and_dict() -> None:
    obj = SimpleNamespace(
        input_tokens=10,
        input_tokens_details=SimpleNamespace(cached_tokens=4),
        output_tokens=7,
        output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        total_tokens=17,
    )
    assert usage_counts(obj) == {
        "input_tokens": 10,
        "cached_tokens": 4,
        "output_tokens": 7,
        "reasoning_tokens": 5,
        "video_frames": 0,
        "images": 0,
    }
    assert usage_counts({"video_frames_processed": 60})["video_frames"] == 60
    assert usage_counts(None) == dict.fromkeys(usage.COUNT_FIELDS, 0)


def test_summarize_missing_and_empty_log() -> None:
    s = summarize()
    assert (s.total_usd, s.n_calls, s.by_kind, s.by_tag) == (0, 0, {}, {})
    out = io.StringIO()
    Console(file=out, width=160).print(s.table())
    assert "no API calls logged yet" in out.getvalue()
    settings.usage_log.parent.mkdir(parents=True)
    settings.usage_log.write_text("")
    assert summarize().n_calls == 0


def test_log_and_summarize() -> None:
    log_usage(kind="wonder", model="m", tag="spec", input_tokens=1000, output_tokens=100)
    log_usage(kind="sam", model="sam-3.1", tag="judge", video_frames=50)
    log_usage(kind="wonder", tag="spec", cached_locally=True, input_tokens=1000, output_tokens=100)
    with settings.usage_log.open("a") as f:
        f.write("not json\n")
    s = summarize()
    one_wonder = wonder_cost(1000, 0, 100)
    assert s.n_calls == 3 and s.total.n_cached == 1
    assert s.total_usd == pytest.approx(one_wonder + 0.01)
    assert s.uncached_usd == pytest.approx(2 * one_wonder + 0.01)
    assert s.by_kind["wonder"].usd == pytest.approx(one_wonder)
    assert s.by_kind["sam"].video_frames == 50
    assert s.by_tag["spec"].n_calls == 2
    assert summarize(tag_prefix="jud").n_calls == 1
    assert summarize(since=time.time() + 60).n_calls == 0
    out = io.StringIO()
    Console(file=out, width=160).print(s.table())
    assert "TOTAL" in out.getvalue() and "kind: sam" in out.getvalue()


def test_log_usage_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        log_usage(kind="vision")


def test_cost_cli_with_missing_log() -> None:
    from showtell.__main__ import app

    result = CliRunner().invoke(app, ["cost"])
    assert result.exit_code == 0, result.output
    assert "Meta Model API usage" in result.output


def test_tag_cached_says_whether_a_stage_was_served_from_the_cache() -> None:
    t0 = time.time()
    log_usage(kind="wonder", tag="job:spec", cached_locally=True, input_tokens=10)
    log_usage(kind="wonder", tag="job:program", input_tokens=10)
    log_usage(kind="wonder", tag="job:program", cached_locally=True, input_tokens=10)
    assert usage.tag_cached("job:spec") is True
    assert usage.tag_cached("job:program") is False  # one of its calls was billed
    assert usage.tag_cached("job:repair1") is None  # no such call
    assert usage.tag_cached("job:spec", since=time.time() + 60) is None
    assert usage.tag_cached("job:spe") is None and usage.tag_cached("job:spec", since=t0) is True
