"""Check the human demo videos before they cost anything (CLAUDE.md 5.4). No API calls.

For each expected file (smoke.mp4 plus every task video in bench/tasks.yaml): present, MP4/MOV,
length, resolution, orientation, and whether it normalizes to the 720p / 10 fps H.264 clip the
pipeline sends to SAM. A contact sheet of 4 normalized frames per video shows the framing (top-down,
bowls along the top edge, every object in view) for a quick look by eye.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel

from showtell.perception import video_prep

MAX_SECONDS = 30.0  # one continuous take under 30 s
SMOKE_MAX_SECONDS = 10.0  # smoke.mp4 is about 5 s
MIN_SHORT_SIDE = 720  # filmed at 1080p; the pipeline downscales to 720p
MIN_FRAMES = 20  # after normalizing to 10 fps: at least 2 s of video
SAM_MAX_FRAMES = 15_000
SUFFIXES = (".mp4", ".mov")
SMOKE = "smoke.mp4"


class VideoCheck(BaseModel):
    name: str
    status: Literal["ok", "warn", "error", "missing"]
    width: int = 0
    height: int = 0
    fps: float = 0.0
    seconds: float = 0.0
    normalized_frames: int = 0
    problems: list[str] = []
    sheet: str | None = None  # contact sheet of 4 normalized frames


def expected_videos() -> list[str]:
    """smoke.mp4, then each task's video file name from bench/tasks.yaml."""
    from showtell.sim.scene import load_tasks

    return [SMOKE, *(t.video for t in load_tasks().values() if t.video)]


def contact_sheet(video: Path, out: Path, n: int = 4, width: int = 360) -> Path:
    """Write `n` evenly spaced frames of `video` side by side as one JPEG."""
    with tempfile.TemporaryDirectory() as tmp:
        frames = video_prep.keyframes(video, n=n, width=width, out_dir=Path(tmp))
        images = [img for img in (cv2.imread(str(p)) for p in frames) if img is not None]
    if not images:
        raise ValueError(f"no frames decoded from {video}")
    height = min(img.shape[0] for img in images)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out), np.hstack([img[:height] for img in images])):
        raise RuntimeError(f"could not write {out}")
    return out


def check_file(
    path: Path, *, max_seconds: float = MAX_SECONDS, sheet_dir: Path | None = None
) -> VideoCheck:
    """Problems that stop the pipeline are errors; ones that make SAM's job harder are warnings."""
    path = Path(path)
    if not path.is_file():
        return VideoCheck(name=path.name, status="missing")
    errors: list[str] = []
    warnings: list[str] = []
    if path.suffix.lower() not in SUFFIXES:
        errors.append(f"{path.suffix or 'no extension'}: SAM needs MP4 or MOV")
    try:
        info = video_prep.probe(path)
    except Exception as err:
        return VideoCheck(
            name=path.name, status="error", problems=[f"cannot read the video: {err}"]
        )
    check = VideoCheck(
        name=path.name,
        status="ok",
        width=info.width,
        height=info.height,
        fps=round(info.fps, 2),
        seconds=round(info.duration, 2),
    )
    if info.duration > max_seconds:
        warnings.append(f"{info.duration:.0f} s long; keep one take under {max_seconds:.0f} s")
    if min(info.width, info.height) < MIN_SHORT_SIDE:
        warnings.append(f"{info.width}x{info.height}: film at 1080p")
    if info.width < info.height:
        warnings.append("portrait: film in landscape so the three bowls fit left to right")
    try:
        normalized = video_prep.normalize(path)
        check.normalized_frames = video_prep.probe(normalized).frames
        if check.normalized_frames > SAM_MAX_FRAMES:
            errors.append(f"{check.normalized_frames} frames at 10 fps; SAM takes at most 15,000")
        elif check.normalized_frames < MIN_FRAMES:
            warnings.append(f"only {check.normalized_frames} frames at 10 fps")
        if sheet_dir is not None:
            check.sheet = str(contact_sheet(normalized, Path(sheet_dir) / f"{path.stem}_sheet.jpg"))
    except Exception as err:
        errors.append(f"could not normalize to 720p / 10 fps H.264: {err}")
    check.problems = errors + warnings
    check.status = "error" if errors else "warn" if warnings else "ok"
    return check


def check_folder(folder: Path, *, sheet_dir: Path | None = None) -> list[VideoCheck]:
    """Every expected video in `folder`, then any other MP4/MOV there (a likely misnamed file)."""
    folder = Path(folder)
    expected = expected_videos()
    results = [
        check_file(
            folder / name,
            max_seconds=SMOKE_MAX_SECONDS if name == SMOKE else MAX_SECONDS,
            sheet_dir=sheet_dir,
        )
        for name in expected
    ]
    for extra in sorted(folder.glob("*")):
        if extra.suffix.lower() in SUFFIXES and extra.name not in expected:
            results.append(
                VideoCheck(
                    name=extra.name,
                    status="warn",
                    problems=[
                        "not a file name from bench/tasks.yaml: rename it to one of the names above"
                    ],
                )
            )
    return results
