"""Phase 2 GO/NO-GO gate on 20 seeds of task 3 (real physics, no videos).

- The hand-written size sort *with* avoid=glass passes all checks on >= 18/20 seeds.
- The same program *without* avoid fails untouched/upright on >= 3/20 seeds.
Run with: pytest -m slow tests/test_phase2_gate.py
"""

from pathlib import Path

import pytest

from showtell.sim.scene import get_task
from showtell.verify.runner import run_seeds, task_seeds

ROOT = Path(__file__).resolve().parent.parent


def _run(program: str):
    task = get_task("task03_size_glass")
    src = (ROOT / "examples" / program).read_text()
    return run_seeds(
        [o.id for o in task.objects],
        src,
        task.reference["checks"],
        task_seeds(task.index, 20),
        record=False,
    )


@pytest.mark.slow
def test_size_sort_with_avoid_passes_at_least_18_of_20() -> None:
    results = _run("size_sort.py")
    assert sum(r.passed for r in results) >= 18


@pytest.mark.slow
def test_size_sort_without_avoid_genuinely_hits_the_glass() -> None:
    results = _run("size_sort_naive.py")
    glass_failures = [
        r
        for r in results
        if any(c.kind in ("untouched", "upright") and not c.passed for c in r.checks)
    ]
    assert len(glass_failures) >= 3
