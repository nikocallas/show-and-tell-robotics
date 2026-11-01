"""Check the human demo videos before they cost anything (CLAUDE.md 5.4). No API calls.

For each expected file (smoke.mp4 plus every task video in bench/tasks.yaml): present under its
exact name, MP4/MOV, length, resolution, orientation, HDR, and whether it normalizes to the 720p /
10 fps H.264 clip the pipeline sends to SAM. A contact sheet of 4 normalized frames per video shows
the framing (top-down, bowls along the top edge, every object in view) for a quick look by eye.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
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
HDR_TRANSFERS = {"arib-std-b67": "HLG", "smpte2084": "PQ"}  # ffprobe color_transfer names
MISNAMED = "not a file name from bench/tasks.yaml: rename it to one of the names above"


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


def hdr_problem(color: Mapping[str, str]) -> str | None:
    """A warning for HDR footage (HLG or PQ transfer, or BT.2020 primaries or matrix; iPhones
    film HLG by default), from `video_prep.probe_color`. The pipeline's 8-bit clip keeps the HDR
    tags without converting the colours, so what SAM tracks and Muse Wonder sees comes out washed
    out and hue-shifted. 10-bit SDR (BT.709) is fine."""
    transfer = color.get("color_transfer", "")
    bt2020 = color.get("color_primaries") == "bt2020" or color.get("color_space", "").startswith(
        "bt2020"
    )
    if transfer not in HDR_TRANSFERS and not bt2020:
        return None
    kind = HDR_TRANSFERS.get(transfer, "BT.2020")
    if "10" in color.get("pix_fmt", ""):
        kind += ", 10-bit"
    return (
        f"HDR video ({kind}): the clip SAM tracks comes out with washed-out, shifted colours; "
        "turn off HDR Video (iPhone: Settings > Camera > Record Video > HDR Video) and film again"
    )


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
        hdr = hdr_problem(video_prep.probe_color(path))
    except Exception as err:
        hdr = f"could not read the colour format: {err}"
    if hdr:
        warnings.append(hdr)
    try:
        normalized = video_prep.normalize(path)
        check.normalized_frames = video_prep.probe(normalized).frames
    except Exception as err:
        errors.append(f"could not normalize to 720p / 10 fps H.264: {err}")
    else:
        if check.normalized_frames > SAM_MAX_FRAMES:
            errors.append(f"{check.normalized_frames} frames at 10 fps; SAM takes at most 15,000")
        elif check.normalized_frames < MIN_FRAMES:
            warnings.append(f"only {check.normalized_frames} frames at 10 fps")
        if sheet_dir is not None:
            try:
                sheet = contact_sheet(normalized, Path(sheet_dir) / f"{path.stem}_sheet.jpg")
                check.sheet = str(sheet)
            except Exception as err:  # the video itself is fine
                warnings.append(f"contact sheet failed: {err}")
    check.problems = errors + warnings
    check.status = "error" if errors else "warn" if warnings else "ok"
    return check


def _match(name: str, files: list[Path]) -> Path | None:
    """The file standing for the expected `name`: that exact name, else the name in another
    case (task02_color.MP4), else the same stem as an .mp4 or .mov (task01_size.MOV)."""
    for same in (
        lambda p: p.name == name,
        lambda p: p.name.lower() == name.lower(),
        lambda p: p.stem.lower() == Path(name).stem.lower() and p.suffix.lower() in SUFFIXES,
    ):
        found = [p for p in files if same(p)]
        if found:
            return found[0]
    return None


def check_folder(folder: Path, *, sheet_dir: Path | None = None) -> list[VideoCheck]:
    """Every expected video in `folder`, then any other MP4/MOV there (a likely misnamed file).

    Names are matched on the folder's actual entries: a file that differs from its expected name
    only in case, or is the same name as a .mov, is checked under the expected name with a
    rename warning (the benchmark opens the exact name; macOS forgives the case, Linux and the
    Docker backend do not). Hidden files (macOS ._ AppleDouble files) are ignored."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"no folder {folder}")
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    results: list[VideoCheck] = []
    for name in expected_videos():
        path = _match(name, files)
        if path is None:
            results.append(VideoCheck(name=name, status="missing"))
            continue
        files.remove(path)
        max_seconds = SMOKE_MAX_SECONDS if name == SMOKE else MAX_SECONDS
        check = check_file(path, max_seconds=max_seconds, sheet_dir=sheet_dir)
        if path.name != name:
            check.problems.append(
                f"named {path.name}: rename it to exactly {name} (Linux and the Docker backend "
                "match names case-sensitively)"
                if path.name.lower() == name.lower()
                else f"found {path.name}: rename it to {name}"
            )
            check.name = name
            if check.status == "ok":
                check.status = "warn"
        results.append(check)
    for extra in files:
        if extra.suffix.lower() in SUFFIXES:
            results.append(VideoCheck(name=extra.name, status="warn", problems=[MISNAMED]))
    return results
