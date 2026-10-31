"""Phase 4: runner.run on real physics, the runtime range cap, the sandbox gate and the per-seed
resource limits (wall clock, memory, skill calls, result size)."""

import multiprocessing as mp
import time
from pathlib import Path

import pytest

from showtell.agent.sandbox import SandboxError
from showtell.agent.schemas import Program, TaskSpec
from showtell.sim import skills
from showtell.sim.scene import get_task
from showtell.verify import runner

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def task1():
    task = get_task("task01_size")
    spec = TaskSpec.model_validate({**task.reference, "rationale": "reference spec"})
    return task, spec, [o.id for o in task.objects]


def test_reference_program_passes_two_seeds(task1) -> None:
    task, spec, object_ids = task1
    source = (ROOT / "examples" / "reference" / "task01_size.py").read_text()
    seeds = runner.task_seeds(task.index, 2)
    seen: list[int] = []
    results = runner.run(
        spec,
        Program(source=source, notes="reference"),
        seeds,
        object_ids=object_ids,
        record=False,
        on_result=lambda r: seen.append(r.seed),
    )
    assert [r.seed for r in results] == seeds and sorted(seen) == seeds
    for r in results:
        assert r.passed and r.error is None, (r.seed, r.error, r.failed_checks)
        assert [c.check_id for c in r.checks] == [c.id for c in spec.checks]
        assert r.videos == {}  # record=False


def test_disguised_infinite_loop_hits_the_range_cap(task1) -> None:
    """range(10**9) written as a literal passes the AST check and is capped at runtime."""
    task, spec, object_ids = task1
    source = "def run(scene):\n    for i in range(1000000000):\n        pick(scene.objects()[0])\n"
    (result,) = runner.run(spec, source, [task.index * 1000], object_ids=object_ids, record=False)
    assert not result.passed
    assert result.error is not None and "range() longer than" in result.error


def test_sandbox_rejects_before_any_worker_starts(task1, monkeypatch) -> None:
    _, spec, object_ids = task1

    class NoWorkers:
        @staticmethod
        def get_context(*args, **kwargs):
            raise AssertionError("a worker was started for a rejected program")

    monkeypatch.setattr(runner, "mp", NoWorkers)
    with pytest.raises(SandboxError, match="imports are not allowed"):
        runner.run(spec, "def run(scene):\n    import os\n", [1000], object_ids=object_ids)
    with pytest.raises(SandboxError, match="arithmetic"):
        runner.run_seeds(object_ids, "def run(scene):\n    x = 10**9\n", spec.checks, [1000])


def test_worker_rechecks_the_program(task1) -> None:
    """Defense in depth: a worker handed an unchecked program reports instead of running it."""
    _, _, object_ids = task1
    parent, child = mp.Pipe(duplex=False)
    runner._worker(
        child,
        {
            "object_ids": object_ids,
            "seed": 7,
            "program_source": "def run(scene):\n    while True:\n        pass\n",
            "checks": [],
            "out_dir": None,
            "record": False,
            "judge_clips": False,
            "hero_size": (640, 480),
        },
    )
    payload = parent.recv()
    assert payload["seed"] == 7 and payload["passed"] is False
    assert payload["error"].startswith("sandbox: line 2: while loops are not allowed")


# ------------------------------------------------------------------------------- resource limits
CPU_BOUND = (  # accepted by the sandbox; runs for minutes unless killed
    "def run(scene):\n"
    "    for a in range(1000):\n"
    "        for b in range(1000):\n"
    "            for c in range(1000):\n"
    "                n = len(scene.objects())\n"
)
MEMORY_HOG = (  # accepted by the sandbox; ~8 MB per iteration, bounded at ~1.6 GB
    "def run(scene):\n"
    "    row = sorted(range(1000))\n"
    "    rows = [row for i in range(1000)]\n"
    "    keep = []\n"
    "    for i in range(200):\n"
    "        keep = [keep, [sorted(r) for r in rows]]\n"
)
AVOID_FLOOD = (  # accepted by the sandbox; used to grow the trace without bound
    "def run(scene):\n"
    "    first = scene.objects()[0]\n"
    "    for a in range(1000):\n"
    "        for b in range(1000):\n"
    f"            avoid(first, margin_m='0.08{' ' * 2000}')\n"
)


def _live_children() -> list:
    return [p for p in mp.active_children() if p.is_alive()]


def test_timeout_kills_a_cpu_bound_program(task1) -> None:
    task, spec, object_ids = task1
    t0 = time.monotonic()
    (result,) = runner.run(
        spec, CPU_BOUND, [task.index * 1000], object_ids=object_ids, record=False, timeout_s=2
    )
    assert not result.passed and result.error is not None
    assert result.error.startswith("timeout: seed took longer than 2 s"), result.error
    assert time.monotonic() - t0 < 20
    assert _live_children() == []


def test_failing_callback_still_kills_running_workers(task1) -> None:
    """on_result raising (e.g. a full disk in the pipeline) must not orphan the other workers."""
    task, spec, object_ids = task1

    def boom(_result: runner.SeedResult) -> None:
        raise OSError("disk full")

    seeds = runner.task_seeds(task.index, 2)
    with pytest.raises(OSError, match="disk full"):
        runner.run(
            spec, CPU_BOUND, seeds, object_ids=object_ids, record=False, workers=2,
            timeout_s=2, on_result=boom,
        )  # fmt: skip
    assert _live_children() == []


def test_memory_limit_stops_a_runaway_program(task1) -> None:
    task, spec, object_ids = task1
    t0 = time.monotonic()
    (result,) = runner.run(
        spec,
        MEMORY_HOG,
        [task.index * 1000],
        object_ids=object_ids,
        record=False,
        timeout_s=60,
        memory_mb=900,
    )
    assert not result.passed and result.error is not None
    # macOS and Linux: the watchdog kills it; Linux may first raise MemoryError (RLIMIT_DATA)
    assert "memory" in result.error.lower(), result.error
    assert time.monotonic() - t0 < 30
    assert _live_children() == []


def test_skill_flood_is_capped_and_the_trace_stays_small(task1) -> None:
    task, spec, object_ids = task1
    (result,) = runner.run(
        spec, AVOID_FLOOD, [task.index * 1000], object_ids=object_ids, record=False
    )
    assert result.error is not None and "too many skill calls" in result.error
    assert f"at most {skills.MAX_SKILL_CALLS}" in result.error
    registered = [e for e in result.trace if e["event"] == "avoid_registered"]
    assert len(registered) == skills.MAX_SKILL_CALLS
    assert all(e["margin_m"] == 0.08 for e in registered)  # the parsed float, not the string
    assert len(result.model_dump_json()) < 100_000


@pytest.fixture()
def robot():
    from showtell.sim.scene import build_model

    model, meta = build_model(get_task("task03_size_glass").objects)
    return skills.Robot(model, meta)


def test_robot_trace_keeps_head_and_tail(robot) -> None:
    n = skills.TRACE_HEAD + skills.TRACE_TAIL + 1234
    for i in range(n):
        robot.log("step", i=i)
    trace = robot.trace
    assert len(trace) == skills.TRACE_HEAD + 1 + skills.TRACE_TAIL
    assert [e["i"] for e in trace[:3]] == [0, 1, 2]
    marker = trace[skills.TRACE_HEAD]
    assert marker["event"] == "trace_truncated" and marker["dropped"] == 1234
    assert trace[-1]["i"] == n - 1 and trace[skills.TRACE_HEAD + 1]["i"] == n - skills.TRACE_TAIL
    robot.reset([])
    assert robot.trace == []


def test_avoid_margin_is_parsed_and_checked(robot) -> None:
    scene, funcs = skills.program_namespace(robot)
    glass = scene.objects(shape="glass")
    funcs["avoid"](glass, margin_m="0.05" + " " * 100)
    assert robot.trace[-1] == {"t": 0.0, "event": "avoid_registered",
                               "obj": glass[0].id, "margin_m": 0.05}  # fmt: skip
    for bad in (float("nan"), -0.1, 5.0, float("inf")):
        with pytest.raises(skills.SkillError, match="margin_m must be between"):
            funcs["avoid"](glass, margin_m=bad)


def test_skill_calls_are_capped_per_run(robot) -> None:
    scene, funcs = skills.program_namespace(robot)
    glass = scene.objects(shape="glass")
    for _ in range(skills.MAX_SKILL_CALLS):
        funcs["avoid"](glass)
    with pytest.raises(skills.SkillError, match="too many skill calls"):
        funcs["avoid"](glass)
    _, fresh = skills.program_namespace(robot)  # a new run starts from zero
    fresh["avoid"](glass)


def test_worker_payload_is_bounded() -> None:
    big = {
        "seed": 1,
        "passed": False,
        "error": "x" * 50_000,
        "trace": [{"event": "e", "i": i} for i in range(5000)],
        "touch_events": [],
    }
    out = runner._bounded(big)
    assert len(out["error"]) < runner.MAX_ERROR_CHARS + 20
    assert len(out["trace"]) == runner.MAX_LIST_ITEMS
    assert out["trace"][-1] == {"event": "truncated", "dropped": 5000 - runner.MAX_LIST_ITEMS + 1}
    assert runner.SeedResult.model_validate(out).seed == 1


def test_rss_of_a_live_and_a_missing_process() -> None:
    import os
    import sys

    rss = runner._rss_bytes(os.getpid())
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        assert rss is not None and rss > 10 * runner.MB
    assert runner._rss_bytes(2**22 + 12345) is None  # no such pid
