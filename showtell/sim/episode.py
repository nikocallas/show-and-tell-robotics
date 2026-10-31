"""One seeded episode: build scene -> run program -> evaluate ground-truth checks -> videos."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from showtell.sim.checks import evaluate_all
from showtell.sim.randomizer import layout
from showtell.sim.render import Recorder
from showtell.sim.scene import ObjSpec, build_model
from showtell.sim.skills import Robot, SkillError, program_namespace

PRE_ROLL_S = 1.0
POST_ROLL_S = 1.0
MAX_RANGE = 1000


def _safe_range(*args: int) -> range:
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise ValueError(f"range() longer than {MAX_RANGE}")
    return r


SAFE_BUILTINS = {"len": len, "sorted": sorted, "range": _safe_range, "min": min, "max": max}


def exec_program(source: str, scene, funcs: dict) -> None:
    """Execute `def run(scene)` with no builtins except the whitelist plus the skill functions.

    Callers that run untrusted code must validate the AST first (showtell.agent.sandbox).
    """
    env: dict[str, Any] = {"__builtins__": dict(SAFE_BUILTINS), **funcs}
    code = compile(source, "<program>", "exec")
    exec(code, env)  # noqa: S102 - validated, builtin-free namespace
    run = env.get("run")
    if not callable(run):
        raise ValueError("program must define run(scene)")
    run(scene)


def run_episode(
    objects: tuple[ObjSpec, ...] | list[ObjSpec],
    seed: int,
    program_source: str,
    checks: list,
    out_dir: Path | None = None,
    record: bool = True,
    judge_clips: bool = True,
    hero_size: tuple[int, int] = (640, 480),
) -> dict:
    """Run one seed. Returns a JSON-serializable result dict (also used by the process pool)."""
    t0 = time.time()
    model, meta = build_model(objects)
    rec = None
    if record:
        if out_dir:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        rec = Recorder(model, out_dir, hero_size=hero_size, record_judge=judge_clips)
    robot = Robot(model, meta, recorder=rec)
    placements = layout(objects, seed)
    robot.reset(placements)
    if rec:
        rec.start_judge_segment()
    robot.idle(PRE_ROLL_S)
    if rec:
        rec.stop_judge_segment()

    error = None
    try:
        robot.go_ready()
        scene, funcs = program_namespace(robot)
        exec_program(program_source, scene, funcs)
    except SkillError as exc:
        error = f"SkillError: {exc}"
    except Exception as exc:  # program bugs: NameError, TypeError, ...
        error = f"{type(exc).__name__}: {exc}"
    if error:
        robot.log("error", message=error)
    try:
        robot.park()
    except Exception as exc:  # pragma: no cover - parking is best effort
        robot.log("park_error", message=str(exc))
    if rec:
        rec.start_judge_segment()
    robot.idle(POST_ROLL_S)
    if rec:
        rec.stop_judge_segment()

    results = evaluate_all(checks, robot)
    videos = rec.finish() if rec else {}
    final = {o.id: [round(float(v), 4) for v in robot.object_xyz(o.id)] for o in meta.objects}
    return {
        "seed": seed,
        "passed": error is None and all(r["passed"] for r in results),
        "checks": results,
        "error": error,
        "videos": videos,
        "sim_seconds": round(float(robot.data.time), 2),
        "wall_seconds": round(time.time() - t0, 2),
        "layout": [list(p) for p in placements],
        "final_positions": final,
        "touches": {k: sorted(v) for k, v in robot.touches.items() if v},
        "touch_events": robot.touch_events,
        "trace": robot.trace,
    }
