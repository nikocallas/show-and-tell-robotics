"""Robot footage for the demo video: real programs on real seeds, rendered at 1920x1080.

Physics is deterministic, so each clip reproduces its run's outcome; `render_clip` returns the
episode's result so the build can assert the outcome the narration states.
"""

from __future__ import annotations

from pathlib import Path

from showtell.agent.schemas import TaskSpec
from showtell.sim.episode import run_episode
from showtell.sim.scene import get_task

SIZE = (1920, 1080)


def reference_checks(task_id: str) -> list:
    task = get_task(task_id)
    return TaskSpec.model_validate({**task.reference, "rationale": "reference"}).checks


def render_clip(
    out_dir: Path, task_id: str, seed: int, program: str, checks: list | None = None
) -> tuple[Path, dict]:
    """(path of the hero MP4, the episode's result dict). Reuses a finished render."""
    task = get_task(task_id)
    out_dir = Path(out_dir)
    result = run_episode(
        task.objects,
        seed,
        program,
        checks if checks is not None else reference_checks(task_id),
        out_dir=out_dir,
        record=True,
        judge_clips=False,
        hero_size=SIZE,
    )
    return out_dir / "full.mp4", result
