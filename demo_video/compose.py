"""Assemble the demo video: narration audio, card and caption images, per-shot MP4s, final cut."""

from __future__ import annotations

import html
import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from demo_video.cdp import Browser

W, H = 1920, 1080
UI_H = 960  # screen capture height; the 120 px below it hold the captions
FPS = 30
SAMPLE_RATE = 48000
VOICE = "Daniel"
RATE_WPM = 184
FONTS = (
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">'
)


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


# ---------------------------------------------------------------------------- narration
def to_speech(text: str) -> str:
    """Spoken form of a caption: names the voice would otherwise mangle."""
    for a, b in (
        ("SAM 3.1", "Sam three point one"),
        ("MuJoCo", "Mew Joe Co"),
        ("20/20", "twenty out of twenty"),
        ("6004", "sixty oh four"),
        ("AST", "A S T"),
    ):
        text = text.replace(a, b)
    return text


def synthesize(text: str, out: Path, voice: str = VOICE, rate: int = RATE_WPM) -> float:
    """One narration line to a mono WAV; returns its length in seconds."""
    out.parent.mkdir(parents=True, exist_ok=True)
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
    run("-i", str(aiff), "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out))
    aiff.unlink()
    return duration_of(out)


def mix_narration(placed: list[tuple[float, Path]], total: float, out: Path) -> Path:
    """Place each WAV at its start time on one mono track."""
    track = np.zeros(int((total + 1) * SAMPLE_RATE), dtype=np.float32)
    for start, path in placed:
        with wave.open(str(path)) as w:
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        i = int(start * SAMPLE_RATE)
        track[i : i + len(data)] += data[: max(0, len(track) - i)]
    track = np.clip(track, -32768, 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(track.tobytes())
    return out


def split_caption(text: str, max_chars: int = 92) -> list[str]:
    """Break a line into caption chunks at clause boundaries, each short enough for two rows."""
    if len(text) <= max_chars:
        return [text]
    parts = re.split(r"(?<=[.:;,?!])\s+", text)
    chunks: list[str] = []
    cur = ""
    for part in parts:
        if cur and len(cur) + 1 + len(part) > max_chars:
            chunks.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        chunks.append(cur)
    out: list[str] = []
    for c in chunks:  # a single clause longer than two rows: split at the middle space
        while len(c) > max_chars * 1.25:
            cut = c.rfind(" ", 0, max_chars)
            out.append(c[:cut])
            c = c[cut + 1 :]
        out.append(c)
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ---------------------------------------------------------------------------- overlays
OVERLAY_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1280px;height:720px;background:transparent;font-family:Inter,-apple-system,sans-serif;overflow:hidden}
.band{position:absolute;left:0;right:0;bottom:0;height:80px;display:flex;align-items:center;justify-content:center;padding:0 90px}
.band.ui{background:#09090b}
.band.clip{height:130px;align-items:flex-end;padding-bottom:18px;
  background:linear-gradient(to top,rgba(9,9,11,.82),rgba(9,9,11,.62) 55%,rgba(9,9,11,0))}
.band p{color:#fafafa;font-size:21.5px;line-height:1.3;font-weight:500;text-align:center;letter-spacing:-.003em;max-width:1100px}
.tag{position:absolute;top:11px;left:61.5%;transform:translateX(-50%);white-space:nowrap;
  background:#fffbeb;color:#92400e;border:1px solid #fcd34d;border-radius:999px;padding:6px 15px;
  font-size:13.5px;font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.code{position:absolute;top:58px;left:22px;background:rgba(9,9,11,.86);color:#e4e4e7;border-radius:11px;
  padding:13px 17px;font-family:'JetBrains Mono',monospace;font-size:12.5px;line-height:1.5;white-space:pre}
.code small{display:block;margin-top:9px;padding-top:8px;border-top:1px solid #3f3f46;color:#a1a1aa;
  font-family:Inter,-apple-system,sans-serif;font-size:11.5px;line-height:1.3}
"""


def code_html(source: str, note: str | None = None) -> str:
    small = f"<small>{html.escape(note)}</small>" if note else ""
    return f'<div class="code">{html.escape(source.strip())}{small}</div>'


def overlay_html(caption: str | None, tag: str | None, speed: str | None, kind: str) -> str:
    parts = [f"<style>{OVERLAY_CSS}</style>{FONTS}"]
    tag = " · ".join(t for t in (tag, speed) if t) or None
    if kind == "ui":
        parts.append(f'<div class="band ui"><p>{html.escape(caption or "")}</p></div>')
    elif caption:
        parts.append(f'<div class="band clip"><p>{html.escape(caption)}</p></div>')
    if tag:
        parts.append(f'<div class="tag">{html.escape(tag)}</div>')
    return "".join(parts)


async def render_overlay(b: Browser, out: Path, markup: str) -> Path:
    """Transparent 1920x1080 PNG of one overlay state."""
    await b.eval(f"document.documentElement.innerHTML = {json.dumps(markup)}; 1")
    await b.eval("document.fonts ? document.fonts.ready.then(() => 1) : 1")
    return await b.screenshot(out, transparent=True)


@dataclass
class Beat:
    """One caption's time range inside a shot."""

    start: float
    end: float
    caption: str


def compose_shot(
    base: Path, overlay_png_times: list[tuple[Path, float]], dur: float, out: Path
) -> Path:
    """Burn a sequence of (overlay PNG, seconds) over the base MP4."""
    listing = out.with_suffix(".overlay.txt")
    lines = ["ffconcat version 1.0"]
    for png, secs in overlay_png_times:
        lines += [f"file '{png.resolve()}'", f"duration {secs:.3f}"]
    lines.append(f"file '{overlay_png_times[-1][0].resolve()}'")
    listing.write_text("\n".join(lines) + "\n")
    run(
        "-i", str(base), "-f", "concat", "-safe", "0", "-i", str(listing),
        "-filter_complex",
        f"[0:v]pad={W}:{H}:0:0:color=#09090b,format=yuv420p[b];"
        f"[1:v]format=rgba,fps={FPS}[o];[b][o]overlay=0:0:format=auto:eof_action=repeat,"
        f"format=yuv420p,fps={FPS},scale=out_range=tv,setparams=range=tv,format=yuv420p[v]",
        "-map", "[v]", "-t", f"{dur:.3f}", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(FPS), str(out),
    )  # fmt: skip
    listing.unlink()
    return out


def base_from_clip(
    src: Path,
    out: Path,
    start: float,
    dur: float,
    speed: float = 1.0,
    size: tuple[int, int] = (W, H),
) -> Path:
    """Trim footage to a shot; the last frame holds if the source runs out."""
    w, h = size
    vf = (
        f"trim=start={start:.3f},setpts=(PTS-STARTPTS)/{speed},scale={w}:{h}:flags=lanczos,"
        f"fps={FPS},tpad=stop_mode=clone:stop_duration={dur + 1:.2f},trim=duration={dur:.3f},"
        "format=yuv420p"
    )
    run(
        "-i",
        str(src),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "16",
        str(out),
    )
    return out


def concat_shots(shots: list[Path], audio: Path | None, out: Path, total: float) -> Path:
    """The final cut. With audio=None it is captions only, with no audio stream."""
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in shots))
    fade_out = max(0.0, total - 0.6)
    video = [
        "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out:.3f}:d=0.6,format=yuv420p",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-pix_fmt", "yuv420p",
    ]  # fmt: skip
    if audio is None:
        inputs, sound = [], ["-an"]
    else:
        inputs = ["-i", str(audio)]
        sound = [
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000",
            "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k",
        ]  # fmt: skip
    run(
        "-f", "concat", "-safe", "0", "-i", str(listing), *inputs,
        *video, *sound, "-movflags", "+faststart", str(out),
    )  # fmt: skip
    listing.unlink()
    return out
