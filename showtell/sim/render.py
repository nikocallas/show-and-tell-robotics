"""Cameras, frame recording and H.264 MP4 writing.

Per seed the runner writes:
- `full.mp4`: hero camera, 30 fps, the whole run (for the UI);
- `judge.mp4` / `judge_angle.mp4`: exactly 50 frames at 25 fps from the overhead / 45-degree camera:
  the first 25 frames of the run + the last 25 (SAM bills a 50-frame minimum per video request).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from showtell.config import settings  # noqa: F401  (sets MUJOCO_GL)

H264_PARAMS = ["-preset", "veryfast", "-crf", "23", "-movflags", "+faststart"]
JUDGE_FPS = 25
JUDGE_HALF = 25  # frames at the start and at the end


def write_mp4(path: Path, frames: list[np.ndarray] | np.ndarray, fps: float) -> Path:
    """Encode RGB uint8 frames (H, W, 3) to an H.264 yuv420p faststart MP4."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = list(frames)
    if not frames:
        raise ValueError(f"no frames for {path}")
    h, w = frames[0].shape[:2]
    writer = Mp4Writer(path, w, h, fps)
    for f in frames:
        writer.write(f)
    writer.close()
    return path


class Mp4Writer:
    """Streaming H.264 writer (frames are piped to ffmpeg as they arrive)."""

    def __init__(self, path: Path, width: int, height: int, fps: float) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._gen = imageio_ffmpeg.write_frames(
            str(self.path),
            (width, height),
            fps=fps,
            codec="libx264",
            pix_fmt_in="rgb24",
            pix_fmt_out="yuv420p",
            quality=None,
            macro_block_size=1,
            output_params=H264_PARAMS,
            ffmpeg_log_level="error",
        )
        self._gen.send(None)
        self.n = 0

    def write(self, frame: np.ndarray) -> None:
        self._gen.send(np.ascontiguousarray(frame, dtype=np.uint8))
        self.n += 1

    def close(self) -> None:
        if self._gen is not None:
            self._gen.close()
            self._gen = None


@dataclass
class Stream:
    camera: str
    width: int
    height: int
    fps: float
    next_t: float = 0.0
    frames: list[np.ndarray] = field(default_factory=list)


class Recorder:
    """Captures camera frames on a sim clock.

    The hero stream is written to disk as it is captured. Judge streams (overhead + judge_angle) are
    only captured while `judge_capture` is on; the runner turns it on for the first and last second.
    """

    def __init__(
        self,
        model,
        out_dir: Path | None,
        hero_size: tuple[int, int] = (640, 480),
        judge_size: tuple[int, int] = (640, 480),
        hero_fps: float = 30.0,
        record_hero: bool = True,
        record_judge: bool = True,
    ) -> None:
        import mujoco

        self.model = model
        self.out_dir = Path(out_dir) if out_dir else None
        self._renderers: dict[tuple[int, int], mujoco.Renderer] = {}
        self.hero = Stream("hero", *hero_size, hero_fps) if record_hero else None
        self.judge = (
            [Stream(c, *judge_size, JUDGE_FPS) for c in ("overhead", "judge_angle")]
            if record_judge
            else []
        )
        self.judge_capture = False
        self._judge_segments: dict[str, list[list[np.ndarray]]] = {s.camera: [] for s in self.judge}
        self._hero_writer: Mp4Writer | None = None
        self.last_hero: np.ndarray | None = None
        if self.hero and self.out_dir:
            self._hero_writer = Mp4Writer(
                self.out_dir / "full.mp4", self.hero.width, self.hero.height, hero_fps
            )

    def _renderer(self, width: int, height: int):
        import mujoco

        key = (width, height)
        if key not in self._renderers:
            self._renderers[key] = mujoco.Renderer(self.model, height=height, width=width)
        return self._renderers[key]

    def render(self, data, camera: str, width: int, height: int) -> np.ndarray:
        r = self._renderer(width, height)
        r.update_scene(data, camera=camera)
        return r.render().copy()

    def start_judge_segment(self) -> None:
        self.judge_capture = True
        for s in self.judge:
            self._judge_segments[s.camera].append([])
            s.next_t = -1.0  # capture immediately

    def stop_judge_segment(self) -> None:
        self.judge_capture = False

    def tick(self, data) -> None:
        t = float(data.time)
        if self.hero and t >= self.hero.next_t:
            frame = self.render(data, "hero", self.hero.width, self.hero.height)
            self.last_hero = frame
            if self._hero_writer:
                self._hero_writer.write(frame)
            self.hero.next_t = (
                self.hero.next_t if self.hero.next_t > 0 else t
            ) + 1.0 / self.hero.fps
        if self.judge_capture:
            for s in self.judge:
                if t >= s.next_t:
                    seg = self._judge_segments[s.camera][-1]
                    if len(seg) < JUDGE_HALF:
                        seg.append(self.render(data, s.camera, s.width, s.height))
                    s.next_t = (s.next_t if s.next_t > 0 else t) + 1.0 / s.fps

    def judge_frames(self, camera: str) -> list[np.ndarray]:
        """First segment's first 25 frames + last segment's last 25 frames (exactly 50)."""
        segs = [s for s in self._judge_segments.get(camera, []) if s]
        if not segs:
            return []
        first, last = segs[0][:JUDGE_HALF], segs[-1][-JUDGE_HALF:]
        first = first + [first[-1]] * (JUDGE_HALF - len(first))
        last = last + [last[-1]] * (JUDGE_HALF - len(last))
        return first + last

    def finish(self) -> dict[str, str]:
        """Close writers and write judge clips. Returns {name: path}."""
        paths: dict[str, str] = {}
        if self._hero_writer:
            self._hero_writer.close()
            paths["full"] = str(self._hero_writer.path)
        if self.out_dir:
            for cam, name in (("overhead", "judge.mp4"), ("judge_angle", "judge_angle.mp4")):
                frames = self.judge_frames(cam)
                if frames:
                    paths[cam] = str(write_mp4(self.out_dir / name, frames, JUDGE_FPS))
            if self.last_hero is not None:
                import imageio.v3 as iio

                thumb = self.out_dir / "thumb.jpg"
                iio.imwrite(thumb, self.last_hero[::2, ::2], quality=85)
                paths["thumb"] = str(thumb)
        for r in self._renderers.values():
            r.close()
        self._renderers.clear()
        return paths


def render_scene_preview(task_id: str, seed: int, out_dir: Path, hd: bool = False) -> list[Path]:
    """PNG from each camera + a 4 s hero MP4 of the seeded layout (Phase 1 acceptance)."""
    import shutil

    import imageio.v3 as iio

    from showtell.sim.randomizer import layout
    from showtell.sim.scene import build_model, get_task
    from showtell.sim.skills import Robot

    task = get_task(task_id)
    model, meta = build_model(task.objects)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hero_size = (1280, 720) if hd else (640, 480)
    tmp = out_dir / f".clip_seed{seed}"
    rec = Recorder(model, tmp, hero_size=hero_size, record_judge=False)
    robot = Robot(model, meta)
    robot.reset(layout(task.objects, seed))
    paths: list[Path] = []
    for cam in ("overhead", "judge_angle", "hero"):
        w, h = hero_size if cam == "hero" else (640, 480)
        p = out_dir / f"seed{seed}_{cam}.png"
        iio.imwrite(p, rec.render(robot.data, cam, w, h))
        paths.append(p)
    # About 4 s (3 s at least): the arm unfolds from its park pose to the ready pose and back.
    robot.recorder = rec
    robot.idle(0.4)
    robot.go_ready()
    robot.park()
    robot.idle(max(0.0, 3.0 - float(robot.data.time)))
    rec.finish()
    clip = out_dir / f"seed{seed}_hero.mp4"
    shutil.move(str(tmp / "full.mp4"), clip)
    shutil.rmtree(tmp, ignore_errors=True)
    paths.append(clip)
    return paths
