"""Run a program on N seeds in parallel, one spawned subprocess per seed (120 s wall-clock timeout).

Each subprocess builds the scene, executes the program in a builtin-free namespace, evaluates the
ground-truth checks and writes the videos (see `showtell.sim.episode`). A crash or timeout still
returns a structured result.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

SEED_TIMEOUT_S = 120.0


class CheckResult(BaseModel):
    check_id: str
    kind: str
    passed: bool
    detail: str
    objects: list[str] = []


class SeedResult(BaseModel):
    seed: int
    passed: bool
    checks: list[CheckResult] = []
    error: str | None = None
    videos: dict[str, str] = {}
    sim_seconds: float = 0.0
    wall_seconds: float = 0.0
    layout: list[list[Any]] = []
    final_positions: dict[str, list[float]] = {}
    touches: dict[str, list[str]] = {}
    touch_events: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def task_seeds(task_index: int, n: int) -> list[int]:
    """Fixed seeds per task so conditions are comparable: task_index*1000 + i."""
    return [task_index * 1000 + i for i in range(n)]


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def _worker(conn, kwargs: dict) -> None:
    try:
        from showtell.sim.episode import run_episode
        from showtell.sim.scene import parse_object_id

        kwargs = dict(kwargs)
        kwargs["objects"] = tuple(parse_object_id(o) for o in kwargs.pop("object_ids"))
        res = run_episode(**kwargs)
        conn.send(_jsonable(res))
    except BaseException as exc:  # report anything, never hang the parent
        conn.send(
            {
                "seed": kwargs.get("seed", -1),
                "passed": False,
                "error": f"worker crashed: {type(exc).__name__}: {exc}\n"
                + traceback.format_exc(limit=3),
            }
        )
    finally:
        conn.close()


def _jsonable(x: Any) -> Any:
    import numpy as np

    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    return x


def run_seeds(
    object_ids: Sequence[str],
    program_source: str,
    checks: list,
    seeds: Sequence[int],
    out_root: Path | None = None,
    record: bool = True,
    judge_clips: bool = True,
    hero_size: tuple[int, int] = (640, 480),
    workers: int | None = None,
    timeout_s: float = SEED_TIMEOUT_S,
    on_result: Callable[[SeedResult], None] | None = None,
) -> list[SeedResult]:
    """Run `program_source` on every seed; results are returned in seed order."""
    ctx = mp.get_context("spawn")
    workers = workers or default_workers()
    checks_payload = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in checks]
    pending = list(seeds)
    running: dict[int, tuple[Any, Any, float]] = {}
    results: dict[int, SeedResult] = {}

    def finish(seed: int, payload: dict) -> None:
        payload.setdefault("seed", seed)
        res = SeedResult.model_validate(payload)
        results[seed] = res
        if on_result:
            on_result(res)

    while pending or running:
        while pending and len(running) < workers:
            seed = pending.pop(0)
            out_dir = Path(out_root) / f"seed_{seed}" if (out_root and record) else None
            parent, child = ctx.Pipe(duplex=False)
            kwargs = dict(
                object_ids=list(object_ids),
                seed=seed,
                program_source=program_source,
                checks=checks_payload,
                out_dir=out_dir,
                record=record,
                judge_clips=judge_clips,
                hero_size=hero_size,
            )
            proc = ctx.Process(target=_worker, args=(child, kwargs), daemon=True)
            proc.start()
            child.close()
            running[seed] = (proc, parent, time.time())
        for seed, (proc, conn, started) in list(running.items()):
            payload = None
            if conn.poll():
                try:
                    payload = conn.recv()
                except EOFError:
                    payload = {"passed": False, "error": "worker exited without a result"}
            elif not proc.is_alive():
                payload = {"passed": False, "error": f"worker died (exit code {proc.exitcode})"}
            elif time.time() - started > timeout_s:
                proc.kill()
                payload = {
                    "passed": False,
                    "error": f"timeout: seed took longer than {timeout_s:.0f} s",
                }
            if payload is not None:
                proc.join(timeout=5)
                conn.close()
                del running[seed]
                finish(seed, payload)
        time.sleep(0.02)
    return [results[s] for s in seeds]
