"""Video prep: probe (ffprobe), normalize to 720p/10 fps H.264 (ffmpeg), keyframes (OpenCV).

ffmpeg outputs are cached under ``cache/video/`` by (input sha256, ffmpeg arguments): reruns
skip the encode, and changed encode settings re-encode. Every run writes its own temp file and
renames it into place under a per-path lock, so concurrent callers (API job threads) never share
or delete each other's files. ``make_test_clip`` synthesizes a small clip for smoke tests.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

import cv2
import numpy as np

from showtell.config import settings
from showtell.meta.cache import file_sha256, key_for

log = logging.getLogger(__name__)

NORMALIZE_VF = "scale=-2:720,fps=10"
X264_OUT = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
# Lossless MP4 copy of the first video stream (for uploads: the Files API documents video/mp4).
REMUX_ARGS = ["-map", "0:v:0", "-c", "copy", "-movflags", "+faststart"]
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class VideoInfo(NamedTuple):
    frames: int
    fps: float
    width: int  # displayed width (after any rotation flag)
    height: int
    duration: float


def ffmpeg_exe() -> str:
    """System ffmpeg if on PATH, else the binary bundled with imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.strip()[-1500:]
        raise RuntimeError(f"{Path(cmd[0]).name} exited {proc.returncode}: {tail}")


@contextmanager
def _path_lock(path: Path) -> Iterator[None]:
    """Serialize work on one output path within this process."""
    key = os.path.abspath(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    with lock:
        yield


def _tmp_beside(path: Path) -> Path:
    """A unique temp path next to ``path`` with its suffix (ffmpeg picks the format from it)."""
    return path.with_name(f".{path.stem}.{uuid.uuid4().hex[:12]}.tmp{path.suffix}")


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_beside(dst)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _ffmpeg_cached(src: Path, args: list[str], what: str) -> Path:
    """``ffmpeg -y -i SRC <args> OUT`` once per (input sha256, args), under ``cache/video/``."""
    src = Path(src)
    name = f"{file_sha256(src)[:32]}_{key_for(args)[:8]}.mp4"
    cached = Path(settings.CACHE_DIR) / "video" / name
    with _path_lock(cached):
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = _tmp_beside(cached)
            try:
                _run([ffmpeg_exe(), "-y", "-i", str(src), *args, str(tmp)])
                os.replace(tmp, cached)
            finally:
                tmp.unlink(missing_ok=True)
            log.info("%s %s -> %s", what, src.name, cached.name)
    return cached


def _rate(text: Any) -> float:
    num, _, den = str(text or "").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    values = [(stream.get("tags") or {}).get("rotate")]
    values += [d.get("rotation") for d in stream.get("side_data_list") or [] if isinstance(d, dict)]
    for v in values:
        try:
            return int(round(float(v))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def _probe_cv2(path: Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"cannot open video {path}")
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    return VideoInfo(frames, fps, w, h, frames / fps if fps else 0.0)


def probe(path: Path) -> VideoInfo:
    """``(frames, fps, width, height, duration)`` of the first video stream."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    exe = shutil.which("ffprobe")
    if exe is None:
        return _probe_cv2(path)
    cmd = [exe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_streams"]
    proc = subprocess.run(
        [*cmd, "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr.strip()[-500:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"no video stream in {path}")
    s = streams[0]
    fps = _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))
    duration = float(s.get("duration") or (data.get("format") or {}).get("duration") or 0.0)
    frames = int(s.get("nb_read_packets") or s.get("nb_frames") or 0)
    if not frames and fps and duration:
        frames = round(fps * duration)
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    if _rotation(s) % 180 == 90:
        w, h = h, w
    return VideoInfo(frames, fps, w, h, duration)


def probe_color(path: Path) -> dict[str, str]:
    """``pix_fmt``, ``color_transfer``, ``color_primaries`` and ``color_space`` of the first video
    stream, as ffprobe names them (fields the file does not tag are left out; {} without
    ffprobe)."""
    exe = shutil.which("ffprobe")
    if exe is None:
        return {}
    fields = "stream=pix_fmt,color_transfer,color_primaries,color_space"
    proc = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-show_entries", fields, "-of", "json"]
        + [str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr.strip()[-500:]}")
    streams = json.loads(proc.stdout or "{}").get("streams") or [{}]
    return {k: v for k, v in streams[0].items() if isinstance(v, str) and v != "unknown"}


def normalize_args() -> list[str]:
    """The ffmpeg arguments between ``-i IN`` and ``OUT`` (also part of the cache key)."""
    # -pix_fmt yuv420p (not in CLAUDE.md's command): phone HDR clips would otherwise come out as
    # 10-bit "High 10" H.264, which browsers cannot play.
    return ["-vf", NORMALIZE_VF, "-an", *X264_OUT, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def normalize(src: Path, out: Path | None = None) -> Path:
    """Run exactly ``ffmpeg -y -i IN -vf "scale=-2:720,fps=10" -an -c:v libx264 -preset veryfast
    -crf 23 -movflags +faststart OUT``, cached by (input sha256, arguments); copy to ``out``
    (atomically) if given."""
    cached = _ffmpeg_cached(Path(src), normalize_args(), "normalized")
    if out is None:
        return cached
    out = Path(out)
    if out.resolve() != cached.resolve():
        _copy_atomic(cached, out)
    return out


def remux_mp4(src: Path) -> Path:
    """A cached MP4 holding ``src``'s first video stream, stream-copied with faststart (no
    re-encode, so frames and timestamps are unchanged; audio and data tracks are dropped)."""
    return _ffmpeg_cached(Path(src), REMUX_ARGS, "remuxed")


def clip_head(src: Path, out: Path, seconds: float = 5.0) -> Path:
    """First ``seconds`` of ``src`` re-encoded as 8-bit H.264 MP4 with faststart (no audio)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [ffmpeg_exe(), "-y", "-i", str(src), "-t", f"{seconds:g}", "-an", *X264_OUT]
        + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    )
    return out


def make_test_clip(out: Path, seconds: float = 5.0, fps: int = 25) -> Path:
    """Synthesize a 640x480 H.264 faststart MP4: an 80 px red square sliding left to right."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d = f"{seconds:g}"
    overlay = f"[0][1]overlay=x=40+(W-w-80)*t/{d}:y=(H-h)/2:eval=frame:shortest=1,format=yuv420p"
    _run(
        [ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"color=c=white:s=640x480:r={fps}:d={d}"]
        + ["-f", "lavfi", "-i", f"color=c=red:s=80x80:r={fps}:d={d}"]
        + ["-filter_complex", overlay, "-an", *X264_OUT]
        + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    )
    return out


def keyframe_indices(n_frames: int, n: int) -> list[int]:
    """``n`` evenly spaced frame indices from first to last frame (fewer if the clip is short)."""
    if n_frames <= 0 or n <= 0:
        return []
    if n == 1:
        return [0]
    return sorted({round(i * (n_frames - 1) / (n - 1)) for i in range(n)})


def _count_frames(video: Path) -> int:
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            raise ValueError(f"cannot open video {video}")
        n = 0
        while cap.grab():
            n += 1
        return n
    finally:
        cap.release()


def _fit_width(frame: np.ndarray, width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= width:
        return frame
    return cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)


def _write_keyframes(video: Path, n: int, width: int, folder: Path) -> list[Path]:
    want = set(keyframe_indices(_count_frames(video), n))
    paths: list[Path] = []
    cap = cv2.VideoCapture(str(video))
    try:
        i = 0
        while want and cap.grab():
            if i in want:
                ok, frame = cap.retrieve()
                if ok:
                    p = folder / f"keyframe_{len(paths):02d}.jpg"
                    img = _fit_width(frame, width)
                    if not cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                        raise RuntimeError(f"could not write keyframe {p}")
                    paths.append(p)
                want.discard(i)
            i += 1
    finally:
        cap.release()
    return paths


def keyframes(video: Path, n: int = 8, width: int = 768, out_dir: Path | None = None) -> list[Path]:
    """Write ``n`` evenly spaced JPEG keyframes (at most ``width`` px wide) and return their
    paths, named ``keyframe_00.jpg``... in time order. Default folder: ``cache/keyframes/``.

    Frames are decoded into a private temp folder and renamed into ``out_dir`` one at a time
    (atomic); only then are older ``keyframe_*.jpg`` files removed. Calls on one folder are
    serialized, so concurrent calls with the same arguments never delete each other's files.
    """
    video = Path(video)
    if out_dir is None:
        out_dir = Path(settings.CACHE_DIR) / "keyframes" / f"{file_sha256(video)[:16]}_{n}_{width}"
    out_dir = Path(out_dir)
    with _path_lock(out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=out_dir, prefix=".tmp-"))
        paths: list[Path] = []
        try:
            for frame in _write_keyframes(video, n, width, tmp):
                paths.append(out_dir / frame.name)
                os.replace(frame, paths[-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        keep = {p.name for p in paths}
        for stale in out_dir.glob("keyframe_*.jpg"):
            if stale.name not in keep:
                stale.unlink(missing_ok=True)
    return paths
