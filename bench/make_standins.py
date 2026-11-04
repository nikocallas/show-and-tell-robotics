"""Stand-in demonstration videos: the robot doing each task, seen by the overhead camera.

A stand-in is a simulator render of the task's hand-written reference program
(examples/reference/), not a recording of a person. It lets the benchmark's video path run on a
task whose human demo is not filmed. It shows the same bin mapping the human demo is to show, on a
layout outside the task's 20 test seeds. File names start with `standin_`, which is how the reports
and the web app label these clips.

No API calls.

    python -m showtell make-standins [--tasks task03_size_glass,task06_color_exc]
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STANDIN_DIR = ROOT / "bench" / "videos" / "standin"
REFERENCE_DIR = ROOT / "examples" / "reference"
STANDIN_FPS = 10
STANDIN_SIZE = (960, 720)
SEED_OFFSET = 990  # task index * 1000 + 990: never one of the task's test seeds


def standin_path(task_id: str) -> Path:
    return STANDIN_DIR / f"standin_{task_id}.mp4"


def standin_seed(task_index: int) -> int:
    return task_index * 1000 + SEED_OFFSET


class OverheadRecorder:
    """The Recorder interface Robot needs: one camera, on the sim clock."""

    def __init__(self, model, fps: float = STANDIN_FPS, size: tuple[int, int] = STANDIN_SIZE):
        import mujoco

        self.renderer = mujoco.Renderer(model, height=size[1], width=size[0])
        self.dt = 1 / fps
        self.next_t = 0.0
        self.frames: list = []

    def tick(self, data) -> None:
        if data.time >= self.next_t:
            self.renderer.update_scene(data, camera="overhead")
            self.frames.append(self.renderer.render().copy())
            self.next_t += self.dt


def render_standin(task_id: str, out: Path | None = None) -> tuple[Path, int]:
    """Render one stand-in clip. Returns (path, frame count)."""
    from showtell.sim.episode import exec_program
    from showtell.sim.randomizer import layout
    from showtell.sim.render import write_mp4
    from showtell.sim.scene import build_model, get_task
    from showtell.sim.skills import Robot, program_namespace

    task = get_task(task_id)
    model, meta = build_model(task.objects)
    recorder = OverheadRecorder(model)
    robot = Robot(model, meta, recorder=recorder)
    robot.reset(layout(task.objects, standin_seed(task.index)))
    robot.idle(1.0)
    robot.go_ready()
    scene, funcs = program_namespace(robot)
    exec_program((REFERENCE_DIR / f"{task_id}.py").read_text(), scene, funcs)
    robot.park()
    robot.idle(1.0)
    path = write_mp4(out or standin_path(task_id), recorder.frames, STANDIN_FPS)
    return path, len(recorder.frames)


def main(tasks: list[str] | None = None) -> list[Path]:
    from showtell.sim.scene import load_tasks

    paths = []
    for task_id in tasks or list(load_tasks()):
        path, n = render_standin(task_id)
        print(f"wrote {path.relative_to(ROOT)} ({n} frames at {STANDIN_FPS} fps)")
        paths.append(path)
    return paths
