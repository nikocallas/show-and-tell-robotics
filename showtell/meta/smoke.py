"""Phase 0 API smoke test: Wonder text, Wonder JSON, Wonder video (or keyframes), SAM tracking.

Writes ``<SHOWTELL_DATA_DIR>/smoke_report.json``. Without ``MODEL_API_KEY`` it writes a report with
``ok=false`` and a clear message instead of raising. By default every call bypasses the local
response cache (``fresh=True``) so a pass proves the live API works.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openai
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from showtell.config import ROOT, settings
from showtell.meta import files, sam, usage, wonder
from showtell.meta.cache import atomic_write_json
from showtell.perception import video_prep

console = Console()
MAIN_STEPS = ("wonder_text", "wonder_json", "wonder_video", "sam")


class SmokeOk(BaseModel):
    ok: bool


def _err(e: BaseException) -> str:
    if isinstance(e, openai.APIStatusError):
        return f"HTTP {e.status_code} {type(e).__name__}: {e.message}"[:600]
    return f"{type(e).__name__}: {e}"[:600]


def _step(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        out = fn()
        out.setdefault("ok", True)
    except Exception as e:  # report and continue with the next step
        out = {"ok": False, "error": _err(e)}
    out["seconds"] = round(time.perf_counter() - t0, 2)
    mark = "[green]PASS[/]" if out["ok"] else "[red]FAIL[/]"
    console.print(
        f"{mark} {name} ({out['seconds']} s)" + (f": {out['error']}" if "error" in out else "")
    )
    return out


def _wonder_text(fresh: bool) -> dict[str, Any]:
    reply = wonder.call_text(
        "Reply with the word ready.",
        effort="low",
        max_output_tokens=1000,
        tag="smoke.text",
        refresh=fresh,
    )
    console.print(f"  Wonder replied: {reply.strip()[:200]!r}")
    return {"ok": "ready" in reply.lower(), "reply": reply.strip()[:500]}


def _wonder_json(fresh: bool) -> dict[str, Any]:
    result = wonder.call_json(
        SmokeOk,
        "You are an API health check. Answer with JSON only.",
        [wonder.text_part('Return a JSON object whose "ok" field is true.')],
        effort="low",
        max_output_tokens=1000,
        tag="smoke.json",
        refresh=fresh,
    )
    mode = "json_schema" if wonder.schema_supported(SmokeOk) else "prompt_only"
    console.print(f"  parsed: {result.model_dump()} via {mode}")
    return {"ok": result.ok is True, "result": result.model_dump(), "structured_output": mode}


def _prepare_clip(video: Path, work: Path) -> dict[str, Any]:
    if video.is_file():
        head = video_prep.clip_head(video, work / "smoke_head.mp4", 5.0)
        concept, source = "hand", str(video)
    else:
        head = video_prep.make_test_clip(work / "synthetic.mp4", 5.0)
        concept, source = "red square", "synthetic (no human clip found)"
    clip = video_prep.normalize(head, work / "smoke_clip.mp4")
    info = video_prep.probe(clip)
    console.print(
        f"  clip: {clip} ({info.frames} frames, {info.width}x{info.height}) from {source}"
    )
    return {"clip": str(clip), "concept": concept, "source": source, **info._asdict()}


def _wonder_video(clip: Path, work: Path, fresh: bool) -> dict[str, Any]:
    kfs = video_prep.keyframes(clip, n=8, out_dir=work / "keyframes")

    def ask(file_id: str) -> tuple[str, str]:
        return wonder.send_media(
            lambda content: wonder.call_text(
                content, effort="low", max_output_tokens=4000, tag="smoke.video", refresh=fresh
            ),
            [wonder.text_part("In one short sentence: what moves in this clip, and which way?")],
            video=wonder.video_file_part(file_id),
            keyframes=kfs,
            tag="smoke.video",
        )

    reply, path = files.with_file(clip, ask)
    console.print(f"  input path: {path}; Wonder said: {reply.strip()[:200]!r}")
    return {
        "ok": bool(reply.strip()),
        "path_used": path,
        "input_video_supported": path == "video",
        "reply": reply.strip()[:500],
        "keyframes": len(kfs),
    }


def _sam(clip: Path, concept: str, fresh: bool) -> dict[str, Any]:
    frames = video_prep.probe(clip).frames
    res = sam.track_full(clip, [concept], tag="smoke.sam", refresh=fresh)[concept]
    first = next((ln for ln in res.raw_text.splitlines() if ln.strip()), "")
    example = None
    if res.tracks:
        oid, boxes = next(iter(res.tracks.items()))
        example = {"object_id": oid, **boxes[0].model_dump()}
    console.print(
        f"  SAM '{concept}': {frames} frames in clip, {res.frames_billed} billed, "
        f"{len(res.tracks)} track(s); first box: {example}"
    )
    out: dict[str, Any] = {
        "ok": bool(res.tracks),
        "concept": concept,
        "frames_in_clip": frames,
        "frames_billed": res.frames_billed,
        "n_tracks": len(res.tracks),
        "track_lengths": {str(k): len(v) for k, v in res.tracks.items()},
        "example_box": example,
        "example_raw_line": sam.short_line(first)[:500],
    }
    if not res.tracks:
        out["note"] = f"zero matches for {concept!r} (a valid API outcome, but the clip has one)"
    return out


def _print_summary(report: dict[str, Any], path: Path) -> None:
    t = Table(title="Smoke test", title_justify="left")
    for col in ("Step", "Status", "Seconds", "Detail"):
        t.add_column(col)
    for name, step in report["steps"].items():
        detail = step.get("error") or step.get("path_used") or step.get("reply") or ""
        if name == "sam" and step.get("ok"):
            detail = f"{step['n_tracks']} track(s), {step['frames_billed']} frames billed"
        status = "[green]ok[/]" if step.get("ok") else "[red]fail[/]"
        t.add_row(name, status, str(step.get("seconds", "")), str(detail)[:80])
    console.print(t)
    console.print(f"cost this run: ${report.get('cost_usd', 0):.4f}; report: {path}")


def run_smoke(
    video: Path = Path("bench/videos/smoke.mp4"), *, fresh: bool = True
) -> dict[str, Any]:
    """Run the four Phase 0 checks and write the JSON report. Returns the report dict."""
    out = Path(settings.SHOWTELL_DATA_DIR) / "smoke_report.json"
    video = Path(video)
    if not video.is_absolute() and not video.exists() and (ROOT / video).exists():
        video = ROOT / video
    t_start = time.time()
    report: dict[str, Any] = {
        "ok": False,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": settings.META_BASE_URL,
        "models": {"wonder": settings.WONDER_MODEL, "sam": settings.SAM_MODEL},
        "openai_sdk": openai.__version__,
        "video_arg": str(video),
        "fresh": fresh,
        "steps": {},
    }
    if not settings.has_api_key:
        report["message"] = "MODEL_API_KEY missing: add it to .env"
        console.print(f"[bold red]{report['message']}[/] (create one at https://dev.meta.ai)")
        atomic_write_json(out, report)
        console.print(f"wrote {out}")
        return report

    work = Path(settings.SHOWTELL_DATA_DIR) / "smoke"
    steps: dict[str, dict[str, Any]] = report["steps"]
    steps["wonder_text"] = _step("Wonder text", lambda: _wonder_text(fresh))
    steps["wonder_json"] = _step("Wonder JSON", lambda: _wonder_json(fresh))
    steps["clip"] = _step("Prepare 5 s clip", lambda: _prepare_clip(video, work))
    if steps["clip"]["ok"]:
        clip, concept = Path(steps["clip"]["clip"]), steps["clip"]["concept"]
        steps["wonder_video"] = _step("Wonder video", lambda: _wonder_video(clip, work, fresh))
        steps["sam"] = _step("SAM video tracking", lambda: _sam(clip, concept, fresh))
    else:
        for name in ("wonder_video", "sam"):
            steps[name] = {"ok": False, "error": "skipped: clip preparation failed"}

    spend = usage.summarize(since=t_start)
    report["cost_usd"] = round(spend.total_usd, 6)
    report["api_calls"] = spend.n_calls
    report["video_path_used"] = steps["wonder_video"].get("path_used")
    report["ok"] = all(steps[k].get("ok") for k in MAIN_STEPS)
    report["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    atomic_write_json(out, report)
    _print_summary(report, out)
    return report
