"""Run a program on N seeds in parallel, one spawned subprocess per seed (120 s wall-clock timeout).

The program must pass the sandbox's AST whitelist (`showtell.agent.sandbox`): `run_seeds` checks it
before starting any worker and raises SandboxError, and each worker checks it again before
executing it. Each subprocess builds the scene, executes the program in a builtin-free namespace,
evaluates the ground-truth checks and writes the videos (see `showtell.sim.episode`). A crash,
timeout or memory overrun still returns a structured result.

Limits per seed: `timeout_s` of wall-clock time, and `memory_mb` of memory (default 2048, or
SHOWTELL_SEED_MEMORY_MB), enforced by a watchdog in the parent that kills a worker whose resident
memory passes it (Linux and macOS), plus RLIMIT_DATA inside the worker on Linux. What a worker
sends back is capped too (trace length, error text), so a program cannot flood the parent.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import multiprocessing as mp
import os
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from showtell.agent.schemas import Program, TaskSpec

SEED_TIMEOUT_S = 120.0
MB = 1024 * 1024
DEFAULT_SEED_MEMORY_MB = 2048.0  # a recorded seed peaks around 550 MB (640x480), 650 MB (720p)
MEMORY_CHECK_S = 0.1
MAX_LIST_ITEMS = 1000  # trace / touch_events entries a worker may send back
MAX_ERROR_CHARS = 2000


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


def seed_memory_mb() -> float:
    """Per-seed memory limit in MB: SHOWTELL_SEED_MEMORY_MB, else 2048 (0 or less = no limit)."""
    try:
        return float(os.environ.get("SHOWTELL_SEED_MEMORY_MB", "") or DEFAULT_SEED_MEMORY_MB)
    except ValueError:
        return DEFAULT_SEED_MEMORY_MB


def _limit_memory(memory_mb: float | None) -> None:
    """Linux: let this worker's private data grow by at most `memory_mb` (RLIMIT_DATA), so a
    runaway program gets a MemoryError. Other systems do not enforce it; the parent's watchdog
    covers them. Must run in the worker process, never in the parent."""
    if not memory_mb or memory_mb <= 0 or not sys.platform.startswith("linux"):
        return
    try:
        import resource

        with open("/proc/self/status", encoding="ascii") as f:
            data_kb = next(int(line.split()[1]) for line in f if line.startswith("VmData:"))
        _, hard = resource.getrlimit(resource.RLIMIT_DATA)
        limit = data_kb * 1024 + int(memory_mb * MB)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_DATA, (limit, hard))
    except (OSError, ValueError, StopIteration):  # best effort: the watchdog still applies
        pass


class _RUsageInfoV0(ctypes.Structure):
    """macOS `struct rusage_info_v0` (sys/resource.h)."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "ri_user_time",
            "ri_system_time",
            "ri_pkg_idle_wkups",
            "ri_interrupt_wkups",
            "ri_pageins",
            "ri_wired_size",
            "ri_resident_size",
            "ri_phys_footprint",
            "ri_proc_start_abstime",
            "ri_proc_exit_abstime",
        )
    ]


@functools.cache
def _libproc() -> Any:
    lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    return lib


def _rss_bytes(pid: int) -> int | None:
    """Resident memory of a live process (Linux /proc, macOS libproc); None if unknown."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/statm", encoding="ascii") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        if sys.platform == "darwin":
            info = _RUsageInfoV0()
            if _libproc().proc_pid_rusage(pid, 0, ctypes.byref(info)) != 0:
                return None
            return int(max(info.ri_resident_size, info.ri_phys_footprint))
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    return None


def _bounded(payload: dict) -> dict:
    """Cap what a worker sends back. Robot already caps its trace; this is defense in depth."""
    for key in ("trace", "touch_events"):
        items = payload.get(key)
        if isinstance(items, list) and len(items) > MAX_LIST_ITEMS:
            marker = {"event": "truncated", "dropped": len(items) - MAX_LIST_ITEMS + 1}
            payload[key] = items[: MAX_LIST_ITEMS - 1] + [marker]
    err = payload.get("error")
    if isinstance(err, str) and len(err) > MAX_ERROR_CHARS:
        payload["error"] = err[:MAX_ERROR_CHARS] + " [truncated]"
    return payload


def _worker(conn, kwargs: dict) -> None:
    try:
        from showtell.agent.sandbox import SandboxError, validate_program
        from showtell.sim.episode import run_episode
        from showtell.sim.scene import parse_object_id

        kwargs = dict(kwargs)
        memory_mb = kwargs.pop("memory_mb", None)
        try:  # defense in depth: never execute a program the sandbox has not accepted
            validate_program(kwargs["program_source"])
        except SandboxError as exc:
            conn.send(
                _bounded(
                    {"seed": kwargs.get("seed", -1), "passed": False, "error": f"sandbox: {exc}"}
                )
            )
            return
        kwargs["objects"] = tuple(parse_object_id(o) for o in kwargs.pop("object_ids"))
        _limit_memory(memory_mb)
        res = run_episode(**kwargs)
        conn.send(_bounded(_jsonable(res)))
    except BaseException as exc:  # report anything, never hang the parent
        conn.send(
            _bounded(
                {
                    "seed": kwargs.get("seed", -1),
                    "passed": False,
                    "error": f"worker crashed: {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc(limit=3),
                }
            )
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
    memory_mb: float | None = None,
) -> list[SeedResult]:
    """Run `program_source` on every seed; results are returned in seed order.

    `memory_mb` caps each seed's memory (None = `seed_memory_mb()`, 0 = no cap). Raises
    SandboxError (before any worker starts) if the program is outside the whitelist. Workers
    still running when this returns or raises (e.g. `on_result` failed) are killed.
    """
    from showtell.agent.sandbox import validate_program

    validate_program(program_source)
    ctx = mp.get_context("spawn")
    workers = workers or default_workers()
    memory_mb = seed_memory_mb() if memory_mb is None else memory_mb
    mem_limit = int(memory_mb * MB) if memory_mb > 0 else 0
    checks_payload = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in checks]
    pending = list(seeds)
    running: dict[int, tuple[Any, Any, float]] = {}
    results: dict[int, SeedResult] = {}
    last_mem_check = 0.0

    def finish(seed: int, payload: dict) -> None:
        payload.setdefault("seed", seed)
        res = SeedResult.model_validate(payload)
        results[seed] = res
        if on_result:
            on_result(res)

    try:
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
                    memory_mb=memory_mb,
                )
                proc = ctx.Process(target=_worker, args=(child, kwargs), daemon=True)
                proc.start()
                child.close()
                running[seed] = (proc, parent, time.time())
            now = time.time()
            check_mem = mem_limit > 0 and now - last_mem_check >= MEMORY_CHECK_S
            if check_mem:
                last_mem_check = now
            for seed, (proc, conn, started) in list(running.items()):
                payload = None
                if conn.poll():
                    try:
                        payload = conn.recv()
                    except EOFError:
                        payload = {"passed": False, "error": "worker exited without a result"}
                elif not proc.is_alive():
                    payload = {"passed": False, "error": f"worker died (exit code {proc.exitcode})"}
                elif now - started > timeout_s:
                    proc.kill()
                    payload = {
                        "passed": False,
                        "error": f"timeout: seed took longer than {timeout_s:.0f} s",
                    }
                elif check_mem and (_rss_bytes(proc.pid) or 0) > mem_limit:
                    proc.kill()
                    payload = {
                        "passed": False,
                        "error": f"memory limit: seed used more than {memory_mb:.0f} MB",
                    }
                if payload is not None:
                    proc.join(timeout=5)
                    conn.close()
                    del running[seed]
                    finish(seed, payload)
            time.sleep(0.02)
    finally:  # never leave workers behind: their timeout is only enforced by this loop
        for proc, conn, _ in running.values():
            if proc.is_alive():
                proc.kill()
            proc.join(timeout=5)
            conn.close()
    return [results[s] for s in seeds]


def run(
    spec: TaskSpec,
    program: Program | str,
    seeds: Sequence[int],
    *,
    object_ids: Sequence[str],
    out_root: Path | None = None,
    record: bool = True,
    **kw: Any,
) -> list[SeedResult]:
    """Run a program on `seeds`, judged by `spec.checks` (the CLAUDE.md 5.7 `runner.run`).

    `kw` goes to run_seeds (judge_clips, hero_size, workers, timeout_s, on_result, memory_mb).
    Raises SandboxError before any worker starts if the program is outside the whitelist.
    """
    source = program if isinstance(program, str) else program.source
    return run_seeds(
        object_ids, source, list(spec.checks), seeds, out_root=out_root, record=record, **kw
    )
