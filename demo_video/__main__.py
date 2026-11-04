"""Build the demo video: python -m demo_video [--only a,b] [--no-capture] [--voice NAME].

Then, to choose its sound:
    python -m demo_video --voiceover my_voice.m4a    the cut with a recorded voice
    python -m demo_video --publish FILE              copy a cut and its captions to the web app

Needs, for a capture: `vite preview` on :4173 (the production build), `npm run dev` on :5173 and
uvicorn on :8000 (see .claude/launch.json). See demo_video/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from demo_video import cards, compose, script, ui_shots
from demo_video.cdp import Browser, capture_to_mp4
from demo_video.clips import render_clip
from showtell.agent.schemas import TaskSpec

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo_video" / "build"
OUT = ROOT / "demo_video" / "out"
SILENT = OUT / "show_and_tell_demo_silent.mp4"
PREVIEW = "preview_macos_voice.mp4"  # never published: see demo_video/README.md
PUBLIC = ROOT / "web" / "public" / "video"
GAP = 0.12  # silence between narration lines of one shot
DARK_PAGE = "data:text/html,<html style='background:%2309090b'></html>"


def narrate(shots: list[script.Shot], voice: str) -> dict[str, list[tuple[Path, float]]]:
    out: dict[str, list[tuple[Path, float]]] = {}
    for s in shots:
        out[s.id] = []
        for i, line in enumerate(s.lines):
            wav = BUILD / "tts" / f"{s.id}_{i}.wav"
            secs = compose.synthesize(compose.to_speech(line), wav, voice=voice)
            out[s.id].append((wav, secs))
    return out


def shot_length(s: script.Shot, lines: list[tuple[Path, float]]) -> float:
    return s.lead + sum(secs for _, secs in lines) + GAP * (len(lines) - 1) + s.tail


def robot_clip(
    name: str,
    task: str,
    seed: int,
    program: str,
    checks: list | None,
    expect: Callable[[dict], None],
) -> Path:
    """The footage of one program on one seed. A clip is rendered again whenever its program
    changes, and its recorded outcome is checked on every build."""
    folder = BUILD / "clips" / name
    stamp = folder / "outcome.json"
    key = hashlib.sha256(program.encode()).hexdigest()
    outcome = json.loads(stamp.read_text()) if stamp.exists() else {}
    if outcome.get("program") != key or not (folder / "full.mp4").exists():
        _, result = render_clip(folder, task, seed, program, checks)
        outcome = {
            "program": key,
            "passed": result["passed"],
            "error": result.get("error"),
            "checks": {c["check_id"]: c["passed"] for c in result["checks"]},
        }
        stamp.write_text(json.dumps(outcome, indent=1))
    expect(outcome)
    return folder / "full.mp4"


def expect_glass_knocked_over(outcome: dict) -> None:
    assert not outcome["passed"], "the naive program was expected to fail on this layout"
    assert not outcome["checks"]["glass_upright"], "the glass was expected to fall"


def expect_pass(outcome: dict) -> None:
    assert outcome["passed"], "Muse Wonder's program was expected to pass on this layout"


def expect_reach_error(outcome: dict) -> None:
    assert not outcome["passed"], "the recorded failing scene was expected to fail again"
    assert "cannot reach pose (0.271, -0.050, 0.550)" in (outcome["error"] or ""), outcome["error"]


def robot_clips(facts: script.Facts, glass_program: str, naive_program: str) -> dict[str, Path]:
    """The naive program and Muse Wonder's program on one layout, and the recorded failing scene."""
    run_checks = TaskSpec.model_validate(facts.repair["spec"]).checks
    return {
        "naive": robot_clip(
            "naive", "task03_size_glass", script.GLASS_SEED, naive_program, None,
            expect_glass_knocked_over,
        ),
        "generated": robot_clip(
            "generated", "task03_size_glass", script.GLASS_SEED, glass_program, None, expect_pass
        ),
        "stuck": robot_clip(
            "stuck", "task06_color_exc", facts.failed_seed["seed"], facts.first["program"],
            run_checks, expect_reach_error,
        ),
    }  # fmt: skip


async def capture_shot(b: Browser, s: script.Shot, dur: float, action) -> tuple[Path, float]:
    """Record one screen shot. Returns (mp4, speed). speed > 1: footage was longer than the shot."""
    cap = await b.start_capture(BUILD / "frames" / s.id)
    t0 = time.time()
    if action:
        await action(b)
    elapsed = time.time() - t0
    speed = s.speed
    if s.extra.get("fit"):  # the shot's length is what the action took (a live wait), sped to fit
        speed = max(1.0, elapsed / dur)
        await asyncio.sleep(0.2)
    else:
        target = dur * speed
        if elapsed > target + 0.05:
            print(f"  {s.id}: actions took {elapsed:.1f} s for a {target:.1f} s shot")
        else:
            await asyncio.sleep(target - elapsed)
    await b.stop_capture()
    mp4 = capture_to_mp4(cap, BUILD / "ui" / f"{s.id}.mp4")
    return mp4, speed


async def capture_card(b: Browser, s: script.Shot, dur: float, markup: str) -> Path:
    page = BUILD / "cards" / f"{s.id}.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(markup)
    # Start from the cards' own dark background: a blank page would flash white as the first frame.
    await b.goto(DARK_PAGE, settle=0.3)
    cap = await b.start_capture(BUILD / "frames" / s.id)
    await b.goto(page.as_uri(), settle=0.0)
    t0 = time.time()
    await asyncio.sleep(max(0.0, dur - (time.time() - t0)))
    await b.stop_capture()
    return capture_to_mp4(
        cap, BUILD / "ui" / f"{s.id}.mp4", start=cap.frames[0][0] if cap.frames else None
    )


def card_markup(name: str) -> str:
    if name == "title":
        return cards.title_card()
    if name == "models":
        return cards.steps_card(
            [
                "Names the objects in the demo",
                "Writes the spec and the checks",
                "Writes the program",
                "Diagnoses failures and patches",
            ],
            ["Tracks objects in the demo video", "Judges every test scene from its rendered clips"],
        )
    if name == "impact":
        return cards.impact_card(
            "Integrating a robot generally runs four to five times the cost of the actual robot.",
            "MIT Sloan, 2021, reporting Julie Shah (MIT).",
            "Who it's for",
            [
                "Line leads",
                "Lab technicians",
                "Small shops without an integrator",
                "Integrators handing over a cell",
            ],
        )
    if name == "end":
        return cards.end_card(
            [
                ("Try the recorded runs", "show-and-tell-robotics.vercel.app"),
                ("Source", "github.com/nikocallas/show-and-tell-robotics"),
                ("Built with", "Muse Wonder · SAM 3.1 · MuJoCo"),
            ],
            "Simulation only so far. Stand-in clips are simulator renders; human demos are not "
            "filmed yet.",
        )
    raise KeyError(name)


def line_starts(s: script.Shot, lines: list[tuple[Path, float]]) -> list[float]:
    """When each narration line of a shot starts, in seconds from the shot's start."""
    out, t = [], s.lead
    for _, secs in lines:
        out.append(t)
        t += secs + GAP
    return out


async def capture_all(
    shots, lengths, narration, only: set[str] | None
) -> dict[str, tuple[Path, float]]:
    """Screen shots in story order, in one browser session: the live job's state carries over."""
    results: dict[str, tuple[Path, float]] = {}
    b = Browser()
    await b.start()
    actions = {
        "form": ui_shots.form,
        "stages": ui_shots.stages,
        "tracked": ui_shots.tracked,
        "spec": ui_shots.spec,
        "program": ui_shots.program,
        "grid_live": ui_shots.grid_live,
        "failure_grid": ui_shots.failure_grid,
        "evidence": ui_shots.evidence,
        "diagnosis": ui_shots.diagnosis,
        "patch": ui_shots.patch,
        "verified": ui_shots.verified,
        "judges": ui_shots.judges,
    }
    bench_shot = next(s for s in shots if s.id == "benchmark")
    actions["benchmark"] = functools.partial(
        ui_shots.benchmark, switch_at=line_starts(bench_shot, narration["benchmark"])[-1]
    )
    before = {
        "tracked": ui_shots.wait_for_approval,
        "failure_grid": ui_shots.before_failure_grid,
        "patch": ui_shots.before_patch,
        "verified": ui_shots.before_verified,
        "judges": ui_shots.before_judges,
        "benchmark": ui_shots.before_benchmark,
    }
    live = {"form", "stages", "tracked", "spec", "program", "grid_live"}
    replay = {"failure_grid", "evidence", "diagnosis", "patch", "verified", "judges"}
    live_open = replay_open = False
    try:
        for s in shots:
            if s.kind == "clip" or (only and s.id not in only):
                continue
            print(f"capture {s.id} ({lengths[s.id]:.1f} s)", flush=True)
            if s.kind == "card":
                mp4 = await capture_card(b, s, lengths[s.id] + 0.3, card_markup(s.extra["card"]))
                results[s.id] = (mp4, 1.0)
                live_open = replay_open = False
                continue
            if s.id in live and not live_open:
                await b.goto(f"{ui_shots.LIVE}/new?live=1", settle=1.5)
                live_open, replay_open = True, False
            if s.id in replay and not replay_open:
                await ui_shots.open_replay(b, script.REPAIR_RUN)
                replay_open, live_open = True, False
            if s.id in before:
                await before[s.id](b)
            results[s.id] = await capture_shot(b, s, lengths[s.id], actions[s.extra["ui"]])
    finally:
        await b.close()
    return results


async def overlays_and_compose(shots, narration, lengths, captured, clips) -> list[Path]:
    """Burn captions and labels into every shot. Returns the shots' MP4s and writes the .srt."""
    b = Browser(width=1280, height=720, port=9334)
    await b.start()
    finals, srt, t_total = [], [], 0.0
    try:
        await b.goto("about:blank", settle=0.1)
        # One throwaway render: the first overlay would otherwise beat the web fonts.
        await compose.render_overlay(
            b,
            BUILD / "fonts_probe.png",
            compose.overlay_html("x", "x", None, "ui") + compose.code_html("x"),
        )
        await asyncio.sleep(1.0)
        for s in shots:
            dur = lengths[s.id]
            base = BUILD / "base" / f"{s.id}.mp4"
            base.parent.mkdir(parents=True, exist_ok=True)
            if s.kind == "clip":
                compose.base_from_clip(clips[s.extra["clip"]], base, s.extra.get("start", 0.0), dur)
                speed = 1.0
            else:
                src, speed = captured[s.id]
                size = (compose.W, compose.UI_H)
                compose.base_from_clip(src, base, 0.0, dur, speed=speed, size=size)
            speed_label = f"sped up {speed:.1f}×" if speed > 1.05 else None
            kind = "clip" if s.kind == "clip" else "ui"
            # Caption chunks with their times inside the shot.
            beats, t = [], s.lead
            for line, (_, secs) in zip(s.lines, narration[s.id], strict=True):
                chunks = compose.split_caption(line)
                total_chars = sum(len(c) for c in chunks)
                for c in chunks:
                    d = secs * len(c) / total_chars
                    beats.append(compose.Beat(t, t + d, c))
                    t += d
                t += GAP
            # Overlay images: one per caption, plus the lead-in and tail states.
            states, cursor = [], 0.0
            for i, beat in enumerate(beats):
                end = beats[i + 1].start if i + 1 < len(beats) else dur
                png = BUILD / "overlays" / f"{s.id}_{i}.png"
                png.parent.mkdir(parents=True, exist_ok=True)
                markup = compose.overlay_html(beat.caption, s.tag, speed_label, kind)
                if s.code:
                    markup += compose.code_html(s.code, script.VIEW_NOTE)
                await compose.render_overlay(b, png, markup)
                states.append((png, end - cursor))
                cursor = end
                srt.append((t_total + beat.start, t_total + beat.end, beat.caption))
            final = BUILD / "shots" / f"{s.id}.mp4"
            final.parent.mkdir(parents=True, exist_ok=True)
            compose.compose_shot(base, states, dur, final)
            finals.append(final)
            t_total += dur
            print(f"composed {s.id}", flush=True)
    finally:
        await b.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "show_and_tell_demo.srt").write_text(
        "\n".join(
            f"{i + 1}\n{compose.srt_time(a)} --> {compose.srt_time(z)}\n{text}\n"
            for i, (a, z, text) in enumerate(srt)
        )
    )
    return finals


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default=None, help="comma-separated shot ids to re-capture")
    ap.add_argument("--no-capture", action="store_true", help="reuse the last screen capture")
    ap.add_argument("--voice", default=compose.VOICE, help="macOS voice for timing and the preview")
    ap.add_argument("--voiceover", type=Path, help="swap a recorded voice into the cut, then stop")
    ap.add_argument("--publish", type=Path, help="copy this cut to web/public/video/, then stop")
    a = ap.parse_args()

    if a.voiceover:
        print(f"wrote {swap_voice(a.voiceover)}")
        return
    if a.publish:
        print(f"wrote {publish(a.publish)}")
        return

    facts = script.load_facts()
    naive_src = (ROOT / "examples" / "size_sort_naive.py").read_text()
    glass_src = facts.bench["tasks"]["task03_size_glass"]["standin"]["final_program"]
    shots = script.shots(facts, glass_src, naive_src)
    narration = narrate(shots, a.voice)
    lengths = {s.id: shot_length(s, narration[s.id]) for s in shots}
    total = sum(lengths.values())
    print(f"narrated length {total:.1f} s")
    assert total < 180, "the demo video must stay under 3 minutes"

    clips = robot_clips(facts, glass_src, naive_src)

    captured_file = BUILD / "captured.json"
    captured: dict[str, tuple[Path, float]] = {}
    if captured_file.exists():
        captured = {
            k: (Path(p), sp) for k, (p, sp) in json.loads(captured_file.read_text()).items()
        }
    if not a.no_capture:
        only = set(a.only.split(",")) if a.only else None
        captured.update(asyncio.run(capture_all(shots, lengths, narration, only)))
        captured_file.write_text(json.dumps({k: [str(p), sp] for k, (p, sp) in captured.items()}))

    finals = asyncio.run(overlays_and_compose(shots, narration, lengths, captured, clips))

    placed, t = [], 0.0
    for s in shots:
        for start, (wav, _) in zip(line_starts(s, narration[s.id]), narration[s.id], strict=True):
            placed.append((t + start, wav))
        t += lengths[s.id]
    audio = compose.mix_narration(placed, total, BUILD / "narration.wav")
    OUT.mkdir(parents=True, exist_ok=True)
    silent = compose.concat_shots(finals, None, SILENT, total)
    print(f"wrote {silent} ({compose.duration_of(silent):.1f} s), captions only")
    preview = compose.concat_shots(finals, audio, OUT / PREVIEW, total)
    print(f"wrote {preview}: for your own review only, see demo_video/README.md")
    write_shot_list(shots, lengths)


def write_shot_list(shots: list[script.Shot], lengths: dict[str, float]) -> None:
    """The generated table in docs/demo_script.md, from the script and the shots' real lengths."""
    rows = ["| Time | On screen | Label | Narration |", "|---|---|---|---|"]
    t = 0.0
    for s in shots:
        end = t + lengths[s.id]
        stamp = f"{int(t) // 60}:{int(t) % 60:02d}–{int(end) // 60}:{int(end) % 60:02d}"
        label = s.tag or "—"
        rows.append(f"| {stamp} | {s.visual} | {label} | {' '.join(s.lines)} |")
        t = end
    doc = ROOT / "docs" / "demo_script.md"
    table = "\n".join(rows) + "\n"
    text = doc.read_text()
    pattern = re.compile(r"(<!-- shots:start -->\n)(.*?)(<!-- shots:end -->)", re.S)
    if pattern.search(text):
        doc.write_text(pattern.sub(lambda m: m.group(1) + table + m.group(3), text))


def swap_voice(audio: Path) -> Path:
    """The captions-only cut with a recorded voice added."""
    out = OUT / "show_and_tell_demo_voiced.mp4"
    length = compose.duration_of(SILENT)  # the whole cut, even if the take ends early
    compose.run(
        "-i", str(SILENT), "-i", str(audio),
        "-map", "0:v", "-map", "1:a", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{length:.3f}",
        "-movflags", "+faststart", str(out),
    )  # fmt: skip
    return out


def publish(cut: Path) -> Path:
    """Put a finished cut and its captions where the hosted demo serves them."""
    assert cut.name != PREVIEW, "this file is for private review only: see demo_video/README.md"
    assert compose.duration_of(cut) < 180, "the demo video must stay under 3 minutes"
    PUBLIC.mkdir(parents=True, exist_ok=True)
    target = PUBLIC / "show-and-tell-demo.mp4"
    shutil.copyfile(cut, target)
    shutil.copyfile(OUT / "show_and_tell_demo.srt", PUBLIC / "show-and-tell-demo.srt")
    return target


if __name__ == "__main__":
    main()
